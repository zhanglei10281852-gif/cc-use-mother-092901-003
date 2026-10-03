"""撤销传播：评审结论撤销后，失去支撑的声明被确定地标记。"""

import json
import unittest

from support import accept, define_scenario, fresh_backend_with_claim, make_run

from scenario_certification import (
    ClaimStatus,
    ScenarioCertificationBackend,
    StateError,
)

AT = "2026-09-20T09:00:00Z"


class RevocationTests(unittest.TestCase):
    def test_verdict_revocation_propagates_to_issued_claim(self):
        backend = fresh_backend_with_claim()
        affected = backend.revoke_verdict("V-R-1", reason="日志完整性存疑",
                                          at=AT, by="reviewer-wang")
        self.assertEqual(affected, ("CLM-1",))
        claim = backend.get_claim("CLM-1")
        self.assertEqual(claim.status, ClaimStatus.AFFECTED)
        self.assertIn("撤销", claim.status_reason)

    def test_revocation_is_idempotent_guard(self):
        backend = fresh_backend_with_claim()
        backend.revoke_verdict("V-R-1", reason="存疑", at=AT, by="reviewer-wang")
        with self.assertRaises(StateError):
            backend.revoke_verdict("V-R-1", reason="重复撤销", at=AT, by="reviewer-wang")

    def test_revoked_verdict_frees_run_for_new_review(self):
        backend = fresh_backend_with_claim()
        backend.revoke_verdict("V-R-1", reason="存疑", at=AT, by="reviewer-wang")
        # 撤销后同一运行可以重新评审
        accept(backend, "R-1", verdict_id="V-R-1-b", at="2026-09-21T09:00:00Z")
        self.assertEqual(backend.active_verdict("R-1").verdict_id, "V-R-1-b")

    def test_revoke_claim_conclusion(self):
        backend = fresh_backend_with_claim()
        claim = backend.revoke_claim("CLM-1", reason="证据链断裂", at=AT,
                                     by="reviewer-wang")
        self.assertEqual(claim.status, ClaimStatus.REVOKED)
        with self.assertRaises(StateError):
            backend.revoke_claim("CLM-1", reason="重复撤销", at=AT, by="reviewer-wang")

    def test_reissue_after_revocation_restores_claim(self):
        backend = fresh_backend_with_claim()
        backend.revoke_verdict("V-R-1", reason="存疑", at=AT, by="reviewer-wang")
        self.assertEqual(backend.get_claim("CLM-1").status, ClaimStatus.AFFECTED)

        # 补做运行并通过评审后重新签发
        backend.record_run(make_run("R-1b", "SC-1", 1, evidence_ids=("EV-1b",)))
        accept(backend, "R-1b", at="2026-09-22T09:00:00Z")
        new_claim = backend.reissue_claim("CLM-1", "CLM-1R",
                                          at="2026-09-23T09:00:00Z", by="reviewer-li")

        self.assertEqual(new_claim.status, ClaimStatus.ISSUED)
        self.assertEqual(new_claim.supersedes, "CLM-1")
        self.assertEqual(backend.get_claim("CLM-1").status, ClaimStatus.SUPERSEDED)

    def test_reissue_requires_invalidated_claim(self):
        backend = fresh_backend_with_claim()
        with self.assertRaises(StateError):
            backend.reissue_claim("CLM-1", "CLM-1R", at=AT, by="reviewer-li")

    def test_multiple_claims_affected_in_sorted_order(self):
        backend = ScenarioCertificationBackend()
        define_scenario(backend, "SC-1")
        backend.record_run(make_run("R-1", "SC-1", 1))
        accept(backend, "R-1")
        for cid in ("CLM-B", "CLM-A", "CLM-C"):
            backend.issue_claim(cid, f"cap-{cid}", "sw-1.0", pins=[("SC-1", 1)],
                                at="2026-09-05T09:00:00Z", by="reviewer-li")
        affected = backend.revoke_verdict("V-R-1", reason="存疑", at=AT,
                                          by="reviewer-wang")
        self.assertEqual(affected, ("CLM-A", "CLM-B", "CLM-C"))

    def test_revocation_propagation_is_deterministic_across_replays(self):
        def script():
            backend = fresh_backend_with_claim()
            backend.record_run(make_run("R-3", "SC-2", 1, evidence_ids=("EV-3",)))
            accept(backend, "R-3")
            backend.issue_claim("CLM-2", "lka-urban", "sw-1.0", pins=[("SC-2", 1)],
                                at="2026-09-06T09:00:00Z", by="reviewer-li")
            backend.revoke_verdict("V-R-1", reason="存疑", at=AT, by="reviewer-wang")
            backend.revoke_claim("CLM-2", reason="复评不通过", at=AT,
                                 by="reviewer-wang")
            return json.dumps(backend.snapshot(), sort_keys=True, ensure_ascii=False)

        self.assertEqual(script(), script())


if __name__ == "__main__":
    unittest.main()
