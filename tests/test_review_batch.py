"""评审批次：冻结、退回局部场景与撤销结论的相互作用。"""

import unittest

from support import accept, define_scenario, make_run

from scenario_certification import (
    BatchStatus,
    ClaimStatus,
    ScenarioCertificationBackend,
    StateError,
)

AT = "2026-09-15T09:00:00Z"


def backend_with_batch():
    backend = ScenarioCertificationBackend()
    define_scenario(backend, "SC-1")
    define_scenario(backend, "SC-2")
    for run_id, sid in (("R-1", "SC-1"), ("R-2", "SC-2")):
        backend.record_run(make_run(run_id, sid, 1))
        accept(backend, run_id)
    backend.create_batch("REL-1", "三季度发布", at="2026-09-04T09:00:00Z")
    backend.issue_claim("CLM-1", "aeb-urban", "sw-1.0",
                        pins=[("SC-1", 1), ("SC-2", 1)], batch_id="REL-1",
                        at="2026-09-05T09:00:00Z", by="reviewer-li")
    backend.issue_claim("CLM-2", "lka-urban", "sw-1.0", pins=[("SC-2", 1)],
                        batch_id="REL-1", at="2026-09-05T09:00:00Z",
                        by="reviewer-li")
    return backend


class ReleaseBatchTests(unittest.TestCase):
    def test_frozen_batch_rejects_new_claims_and_returns(self):
        backend = backend_with_batch()
        backend.freeze_batch("REL-1", at=AT, by="reviewer-wang")
        self.assertEqual(backend.get_batch("REL-1").status, BatchStatus.FROZEN)

        with self.assertRaises(StateError):
            backend.issue_claim("CLM-3", "acc-urban", "sw-1.0", pins=[("SC-1", 1)],
                                batch_id="REL-1", at=AT, by="reviewer-li")
        with self.assertRaises(StateError):
            backend.return_scenarios("REL-1", ["SC-1"], reason="复测", at=AT,
                                     by="reviewer-wang")
        with self.assertRaises(StateError):
            backend.freeze_batch("REL-1", at=AT, by="reviewer-wang")

    def test_return_scenarios_marks_only_covering_claims(self):
        backend = backend_with_batch()
        returned = backend.return_scenarios(
            "REL-1", ["SC-1"], reason="路口场景需要复测", at=AT, by="reviewer-wang")

        self.assertEqual(returned, ("CLM-1",))  # CLM-2 不覆盖 SC-1，不受影响
        claim = backend.get_claim("CLM-1")
        self.assertEqual(claim.status, ClaimStatus.RETURNED)
        self.assertEqual(claim.returned_scenarios, ("SC-1",))
        self.assertEqual(backend.get_claim("CLM-2").status, ClaimStatus.ISSUED)

    def test_returned_claim_can_be_reissued(self):
        backend = backend_with_batch()
        backend.return_scenarios("REL-1", ["SC-1"], reason="复测", at=AT,
                                 by="reviewer-wang")
        new_claim = backend.reissue_claim("CLM-1", "CLM-1R",
                                          at="2026-09-16T09:00:00Z",
                                          by="reviewer-li")
        self.assertEqual(new_claim.status, ClaimStatus.ISSUED)
        self.assertEqual(backend.get_claim("CLM-1").status, ClaimStatus.SUPERSEDED)

    def test_revocation_remains_possible_inside_frozen_batch(self):
        backend = backend_with_batch()
        backend.freeze_batch("REL-1", at=AT, by="reviewer-wang")
        # 冻结只锁定编辑，安全撤销仍然可用
        claim = backend.revoke_claim("CLM-2", reason="发布后发现证据链断裂",
                                     at="2026-09-17T09:00:00Z", by="reviewer-wang")
        self.assertEqual(claim.status, ClaimStatus.REVOKED)

    def test_verdict_revocation_propagates_into_frozen_batch(self):
        backend = backend_with_batch()
        backend.freeze_batch("REL-1", at=AT, by="reviewer-wang")
        affected = backend.revoke_verdict("V-R-1", reason="日志完整性存疑",
                                          at="2026-09-17T09:00:00Z",
                                          by="reviewer-wang")
        self.assertEqual(affected, ("CLM-1",))
        self.assertEqual(backend.get_claim("CLM-1").status, ClaimStatus.AFFECTED)


if __name__ == "__main__":
    unittest.main()
