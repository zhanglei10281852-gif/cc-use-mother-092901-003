"""覆盖计算：合格运行判定与重复上传去重。"""

import unittest

from support import accept, define_scenario, make_run

from scenario_certification import (
    CoverageError,
    ReviewVerdict,
    RunOutcome,
    ScenarioCertificationBackend,
    VerdictDecision,
)

AT = "2026-09-05T09:00:00Z"


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.backend = ScenarioCertificationBackend()
        define_scenario(self.backend, "SC-1")
        define_scenario(self.backend, "SC-2")

    def _issue(self, pins, claim_id="CLM-1"):
        return self.backend.issue_claim(claim_id, "aeb-urban", "sw-1.0",
                                        pins=pins, at=AT, by="reviewer-li")

    def test_issue_requires_accepted_passing_run_on_every_pin(self):
        self.backend.record_run(make_run("R-1", "SC-1", 1))
        accept(self.backend, "R-1")
        # SC-2 没有运行 → 签发被拒并指出缺口
        with self.assertRaises(CoverageError) as ctx:
            self._issue([("SC-1", 1), ("SC-2", 1)])
        self.assertIn("SC-2@v1", str(ctx.exception))

        self.backend.record_run(make_run("R-2", "SC-2", 1))
        accept(self.backend, "R-2")
        claim = self._issue([("SC-1", 1), ("SC-2", 1)])
        report = self.backend.coverage_report(claim.claim_id)
        self.assertTrue(report.complete)
        self.assertEqual((report.covered, report.total), (2, 2))

    def test_failed_inconclusive_and_unreviewed_runs_do_not_count(self):
        self.backend.record_run(make_run("R-F", "SC-1", 1, outcome=RunOutcome.FAILED))
        self.backend.record_run(make_run("R-I", "SC-1", 1, outcome=RunOutcome.INCONCLUSIVE))
        self.backend.record_run(make_run("R-P", "SC-1", 1))  # 通过但未评审
        with self.assertRaises(CoverageError):
            self._issue([("SC-1", 1)])

    def test_rejected_verdict_does_not_count(self):
        self.backend.record_run(make_run("R-1", "SC-1", 1))
        self.backend.submit_verdict(ReviewVerdict(
            "V-1", "R-1", VerdictDecision.REJECTED, "reviewer-li",
            "日志缺失", "2026-09-03T09:00:00Z"))
        with self.assertRaises(CoverageError):
            self._issue([("SC-1", 1)])

    def test_software_version_must_match(self):
        self.backend.record_run(make_run("R-1", "SC-1", 1, software="sw-old"))
        accept(self.backend, "R-1")
        with self.assertRaises(CoverageError):
            self._issue([("SC-1", 1)])  # 声明针对 sw-1.0，旧软件的运行不计入

    def test_duplicate_upload_does_not_inflate_coverage(self):
        self.backend.record_run(make_run("R-1", "SC-1", 1))
        accept(self.backend, "R-1")
        claim = self._issue([("SC-1", 1)])
        before = self.backend.coverage_report(claim.claim_id)

        # 同一运行重复上传两次
        self.backend.record_run(make_run("R-1", "SC-1", 1))
        self.backend.record_run(make_run("R-1", "SC-1", 1))
        after = self.backend.coverage_report(claim.claim_id)

        self.assertEqual(before, after)
        self.assertEqual(after.pins[0].qualifying_runs, ("R-1",))

    def test_distinct_runs_each_count_once(self):
        self.backend.record_run(make_run("R-1", "SC-1", 1))
        self.backend.record_run(make_run("R-2", "SC-1", 1, evidence_ids=("EV-2",)))
        accept(self.backend, "R-1")
        accept(self.backend, "R-2")
        claim = self._issue([("SC-1", 1)])
        report = self.backend.coverage_report(claim.claim_id)
        self.assertEqual(report.pins[0].qualifying_runs, ("R-1", "R-2"))


if __name__ == "__main__":
    unittest.main()
