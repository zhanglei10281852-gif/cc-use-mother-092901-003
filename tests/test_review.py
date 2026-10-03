import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from scenario_certification import (
    ClaimStatus,
    ConclusionState,
    ScenarioRef,
    StateError,
    ValidationError,
)
from support import add_run, issue_claim, make_content, new_backend, register_scenario


class ReviewWorkflowTests(unittest.TestCase):
    """评审：批次冻结、局部场景退回、结论撤销及其传播。"""

    def _backend_with_two_scenario_claim(self):
        backend = new_backend()
        register_scenario(backend, "SCN-1", tag="urban")
        register_scenario(backend, "SCN-2", tag="highway")
        backend.claims.register_capability("CAP-1", "领航辅助", ("SCN-1", "SCN-2"))
        run1 = add_run(backend, "SCN-1", 1).run
        run2 = add_run(backend, "SCN-2", 1).run
        claim = backend.claims.draft_claim("CAP-1", (run1.run_id, run2.run_id))
        conclusion = backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-a", "覆盖充分")
        return backend, backend.claims.require_claim(claim.claim_id), conclusion

    def test_frozen_batch_locks_membership(self):
        backend, claim, _ = self._backend_with_two_scenario_claim()
        batch = backend.review.create_batch((claim.claim_id,))
        backend.review.freeze_batch(batch.batch_id)

        claim2, _ = issue_claim(backend, "CAP-2", "SCN-9")
        with self.assertRaises(StateError):
            backend.review.add_claims(batch.batch_id, (claim2.claim_id,))
        with self.assertRaises(StateError):
            backend.review.freeze_batch(batch.batch_id)

    def test_return_partial_scenarios_then_reinstate_after_new_evidence(self):
        backend, claim, _ = self._backend_with_two_scenario_claim()
        batch = backend.review.create_batch((claim.claim_id,))
        backend.review.freeze_batch(batch.batch_id)

        returned = backend.review.return_scenarios(
            batch.batch_id, claim.claim_id, (ScenarioRef("SCN-2", 1),), "reviewer-b", "高速场景阈值存疑"
        )
        self.assertIs(returned.status, ClaimStatus.SUSPENDED)
        self.assertEqual(returned.returned_refs, (ScenarioRef("SCN-2", 1),))

        # 未补证直接重新签发会被拒绝
        with self.assertRaises(ValidationError):
            backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-a", "直接放行")

        # 场景更新到新版本并补跑通过后，追加运行并重新评审签发
        backend.publish_revision("SCN-2", 1, make_content("highway", ttc=2.0), "qa", "收紧阈值")
        fix_run = add_run(backend, "SCN-2", 2, ev="ev-fix").run
        backend.claims.attach_runs(claim.claim_id, (fix_run.run_id,))
        backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-a", "补证通过")

        reinstated = backend.claims.require_claim(claim.claim_id)
        self.assertIs(reinstated.status, ClaimStatus.ISSUED)
        self.assertEqual(reinstated.returned_refs, ())

    def test_revocation_propagates_only_to_dependent_claim(self):
        backend, claim1, conclusion1 = self._backend_with_two_scenario_claim()
        claim2, _ = issue_claim(backend, "CAP-2", "SCN-9")
        batch = backend.review.create_batch((claim1.claim_id, claim2.claim_id))
        backend.review.freeze_batch(batch.batch_id)

        backend.review.revoke_conclusion(conclusion1.conclusion_id, "reviewer-c", "评审依据失效")

        self.assertIs(backend.claims.require_claim(claim1.claim_id).status, ClaimStatus.REVOKED)
        self.assertIs(backend.claims.require_claim(claim2.claim_id).status, ClaimStatus.ISSUED)
        self.assertIs(
            backend.store.conclusions[conclusion1.conclusion_id].state,
            ConclusionState.REVOKED,
        )
        # 批次成员不变，撤销体现在声明状态上
        self.assertEqual(backend.store.batches[batch.batch_id].claim_ids, (claim1.claim_id, claim2.claim_id))

        with self.assertRaises(StateError):
            backend.review.revoke_conclusion(conclusion1.conclusion_id, "reviewer-c", "重复撤销")

    def test_revoking_superseded_conclusion_leaves_reinstated_claim(self):
        backend, claim, conclusion1 = self._backend_with_two_scenario_claim()
        batch = backend.review.create_batch((claim.claim_id,))
        backend.review.return_scenarios(
            batch.batch_id, claim.claim_id, (ScenarioRef("SCN-1", 1),), "reviewer-b", "城区场景存疑"
        )
        fix_run = add_run(backend, "SCN-1", 1, digest="input-fix", ev="ev-fix").run
        backend.claims.attach_runs(claim.claim_id, (fix_run.run_id,))
        backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-a", "补证通过")

        # 旧结论被撤销，但声明已依据新结论签发，不受影响
        backend.review.revoke_conclusion(conclusion1.conclusion_id, "reviewer-c", "旧结论作废")
        self.assertIs(backend.claims.require_claim(claim.claim_id).status, ClaimStatus.ISSUED)

    def test_return_rejects_refs_outside_claim(self):
        backend, claim, _ = self._backend_with_two_scenario_claim()
        batch = backend.review.create_batch((claim.claim_id,))
        with self.assertRaises(ValidationError):
            backend.review.return_scenarios(
                batch.batch_id, claim.claim_id, (ScenarioRef("SCN-1", 2),), "reviewer-b", "越界引用"
            )


if __name__ == "__main__":
    unittest.main()
