import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from scenario_certification import NotFoundError, RunOutcome
from support import add_run, new_backend, register_scenario


class RunCoverageTests(unittest.TestCase):
    """覆盖去重与失败重跑：重复上传不增覆盖率，重跑不覆盖原始结果。"""

    def setUp(self):
        self.backend = new_backend()
        register_scenario(self.backend, "SCN-1")
        self.backend.claims.register_capability("CAP-1", "自动紧急制动", ("SCN-1",))

    def test_duplicate_upload_is_idempotent_and_keeps_coverage(self):
        first = add_run(self.backend, "SCN-1", 1)
        second = add_run(self.backend, "SCN-1", 1)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.run.run_id, second.run.run_id)
        self.assertEqual(len(self.backend.store.runs), 1)

        report = self.backend.claims.coverage_report("CAP-1")
        self.assertEqual(report.covered_count, 1)
        self.assertTrue(report.complete)

    def test_failed_rerun_appends_without_overwriting_original(self):
        passed = add_run(self.backend, "SCN-1", 1, RunOutcome.PASSED).run
        failed = add_run(self.backend, "SCN-1", 1, RunOutcome.FAILED, ev="ev-rerun").run

        self.assertNotEqual(passed.run_id, failed.run_id)
        self.assertEqual((passed.attempt, failed.attempt), (1, 2))
        # 原始记录未被覆盖，仍保持通过
        self.assertIs(self.backend.store.runs[passed.run_id].outcome, RunOutcome.PASSED)
        self.assertIs(self.backend.store.runs[failed.run_id].outcome, RunOutcome.FAILED)
        # 覆盖率按“存在通过运行”推导，失败重跑不会撤销既有覆盖
        report = self.backend.claims.coverage_report("CAP-1")
        self.assertEqual(report.covered_count, 1)
        self.assertTrue(report.complete)

    def test_failed_first_then_passed_rerun_covers(self):
        add_run(self.backend, "SCN-1", 1, RunOutcome.FAILED)
        report = self.backend.claims.coverage_report("CAP-1")
        self.assertEqual(report.covered_count, 0)
        self.assertEqual(report.missing_scenarios, ("SCN-1",))

        passed = add_run(self.backend, "SCN-1", 1, RunOutcome.PASSED, ev="ev-fix").run
        self.assertEqual(passed.attempt, 2)
        report = self.backend.claims.coverage_report("CAP-1")
        self.assertTrue(report.complete)

    def test_coverage_dedupes_multiple_runs_on_same_revision(self):
        add_run(self.backend, "SCN-1", 1, ev="ev-a")
        add_run(self.backend, "SCN-1", 1, digest="input-2", ev="ev-b")
        self.assertEqual(len(self.backend.store.runs), 2)
        report = self.backend.claims.coverage_report("CAP-1")
        self.assertEqual(report.covered_count, 1)

    def test_run_on_unknown_revision_rejected(self):
        with self.assertRaises(NotFoundError):
            add_run(self.backend, "SCN-1", 99)
        with self.assertRaises(NotFoundError):
            add_run(self.backend, "SCN-X", 1)


if __name__ == "__main__":
    unittest.main()
