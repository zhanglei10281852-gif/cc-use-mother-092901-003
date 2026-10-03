"""运行录入：幂等去重、不可覆盖、失败重跑链接。"""

import unittest

from support import define_scenario, make_run

from scenario_certification import (
    ConflictError,
    NotFoundError,
    RunOutcome,
    ScenarioCertificationBackend,
)


class RunIngestionTests(unittest.TestCase):
    def setUp(self):
        self.backend = ScenarioCertificationBackend()
        define_scenario(self.backend, "SC-1")

    def test_identical_reupload_is_idempotent(self):
        run = make_run("R-1", "SC-1", 1)
        _, created = self.backend.record_run(run)
        self.assertTrue(created)

        again = make_run("R-1", "SC-1", 1)  # 内容完全相同的重传
        record, created = self.backend.record_run(again)
        self.assertFalse(created)
        self.assertEqual(record.run_id, "R-1")
        kinds = [e.kind for e in self.backend.events()]
        self.assertEqual(kinds.count("run_recorded"), 1)
        self.assertEqual(kinds.count("run_duplicate_ignored"), 1)

    def test_same_id_different_content_is_rejected_not_overwritten(self):
        self.backend.record_run(make_run("R-1", "SC-1", 1, outcome=RunOutcome.PASSED))
        with self.assertRaises(ConflictError):
            self.backend.record_run(
                make_run("R-1", "SC-1", 1, outcome=RunOutcome.FAILED))
        # 原始结果保持 PASSED，失败重跑不能覆盖
        self.assertEqual(self.backend.get_run("R-1").outcome, RunOutcome.PASSED)

    def test_failed_rerun_is_new_record_linked_to_original(self):
        self.backend.record_run(make_run("R-1", "SC-1", 1, outcome=RunOutcome.PASSED))
        self.backend.record_run(make_run("R-2", "SC-1", 1, outcome=RunOutcome.FAILED,
                                         rerun_of="R-1"))

        original = self.backend.get_run("R-1")
        self.assertEqual(original.outcome, RunOutcome.PASSED)
        trace = self.backend.trace_run("R-1")
        self.assertEqual(trace["reruns"], ["R-2"])
        self.assertEqual(self.backend.trace_run("R-2")["rerun_of"], "R-1")

    def test_run_referencing_missing_scenario_version_rejected(self):
        with self.assertRaises(NotFoundError):
            self.backend.record_run(make_run("R-9", "SC-1", 7))

    def test_rerun_link_must_point_to_existing_run(self):
        with self.assertRaises(NotFoundError):
            self.backend.record_run(make_run("R-2", "SC-1", 1, rerun_of="R-MISSING"))


if __name__ == "__main__":
    unittest.main()
