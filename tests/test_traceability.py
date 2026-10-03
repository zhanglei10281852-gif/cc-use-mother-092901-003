"""双向追溯：能力 → 场景/运行/证据，以及反向查询。"""

import unittest

from support import accept, define_scenario, expected, fresh_backend_with_claim, make_run

from scenario_certification import ClaimStatus, NotFoundError


class TraceabilityTests(unittest.TestCase):
    def test_claim_traces_to_exact_scenario_version_run_and_evidence(self):
        backend = fresh_backend_with_claim()
        trace = backend.trace_claim("CLM-1")

        self.assertEqual(trace["status"], "issued")
        self.assertEqual(trace["software_version"], "sw-1.0")
        pins = {(s["scenario_id"], s["version"]) for s in trace["scenarios"]}
        self.assertEqual(pins, {("SC-1", 1), ("SC-2", 1)})
        self.assertEqual(trace["runs"], ["R-1", "R-2"])
        self.assertEqual(trace["evidence"], ["EV-1"])
        for s in trace["scenarios"]:
            self.assertEqual(len(s["content_digest"]), 64)

    def test_scenario_traces_back_to_runs_and_claims(self):
        backend = fresh_backend_with_claim()
        trace = backend.trace_scenario("SC-1", 1)
        version = trace["versions"][0]
        self.assertEqual(version["runs"], ["R-1"])
        self.assertEqual(version["claims"], ["CLM-1"])

    def test_run_traces_to_verdict_and_supported_claims(self):
        backend = fresh_backend_with_claim()
        trace = backend.trace_run("R-1")
        self.assertEqual(trace["verdict"]["verdict_id"], "V-R-1")
        self.assertEqual(trace["verdict"]["decision"], "accepted")
        self.assertEqual(trace["supported_claims"], ["CLM-1"])

    def test_evidence_traces_back_to_run_and_claim(self):
        backend = fresh_backend_with_claim()
        trace = backend.trace_evidence("EV-1")
        self.assertEqual(sorted(trace["runs"]), ["R-1", "R-2"])
        self.assertEqual(trace["claims"], ["CLM-1"])
        with self.assertRaises(NotFoundError):
            backend.trace_evidence("EV-MISSING")

    def test_trace_shows_exact_version_after_scenario_fork(self):
        backend = fresh_backend_with_claim()
        backend.fork_scenario("SC-1", reason="阈值收紧", at="2026-09-10T09:00:00Z",
                              by="team", expected=(expected(threshold=2.0),))
        # 管理层可以确认：该能力是在 SC-1@v1 上验证的，且声明已被标记受影响
        trace = backend.trace_claim("CLM-1")
        self.assertEqual(trace["status"], ClaimStatus.AFFECTED.value)
        versions = {(s["scenario_id"], s["version"]) for s in trace["scenarios"]}
        self.assertIn(("SC-1", 1), versions)

    def test_trace_outputs_are_sorted(self):
        backend = fresh_backend_with_claim()
        # 额外声明与运行，验证输出顺序稳定
        backend.record_run(make_run("R-0", "SC-1", 1, evidence_ids=("EV-0",)))
        accept(backend, "R-0")
        trace = backend.trace_scenario("SC-1", 1)
        self.assertEqual(trace["versions"][0]["runs"], ["R-0", "R-1"])


if __name__ == "__main__":
    unittest.main()
