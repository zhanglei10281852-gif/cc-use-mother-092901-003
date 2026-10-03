import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from scenario_certification import ClaimStatus
from support import add_run, issue_claim, make_content, new_backend, register_scenario


class ImpactAnalysisTests(unittest.TestCase):
    """场景变更影响：只有引用被超越祖先版本的已签发声明才受影响。"""

    def test_impact_follows_lineage_not_sibling_fork(self):
        backend = new_backend()
        register_scenario(backend, "SCN-1", tag="v1")
        backend.claims.register_capability("CAP-1", "自动紧急制动", ("SCN-1",))
        run_v1 = add_run(backend, "SCN-1", 1).run
        claim_v1 = backend.claims.draft_claim("CAP-1", (run_v1.run_id,))
        backend.review.submit_conclusion(claim_v1.claim_id, "approve", "reviewer-a", "通过")

        impact = backend.publish_revision("SCN-1", 1, make_content("v2"), "qa", "主分支演进")
        self.assertEqual(impact.impacted_claim_ids, (claim_v1.claim_id,))

        # 在 v2 上签发的声明不受 v1 的另一分叉影响
        run_v2 = add_run(backend, "SCN-1", 2, digest="input-2", ev="ev-2").run
        claim_v2 = backend.claims.draft_claim("CAP-1", (run_v2.run_id,))
        backend.review.submit_conclusion(claim_v2.claim_id, "approve", "reviewer-a", "通过")

        fork = backend.publish_revision(
            "SCN-1", 1, make_content("v2-fork", fault="sensor-dropout"), "qa", "故障分叉"
        )
        self.assertEqual(fork.impacted_claim_ids, (claim_v1.claim_id,))
        self.assertNotIn(claim_v2.claim_id, fork.impacted_claim_ids)

    def test_draft_and_revoked_claims_are_not_reported(self):
        backend = new_backend()
        register_scenario(backend, "SCN-1", tag="v1")
        backend.claims.register_capability("CAP-1", "自动紧急制动", ("SCN-1",))
        run = add_run(backend, "SCN-1", 1).run

        draft = backend.claims.draft_claim("CAP-1", (run.run_id,))
        issued = backend.claims.draft_claim("CAP-1", (run.run_id,))
        conclusion = backend.review.submit_conclusion(issued.claim_id, "approve", "reviewer-a", "通过")
        revoked = backend.claims.draft_claim("CAP-1", (run.run_id,))
        con_revoked = backend.review.submit_conclusion(revoked.claim_id, "approve", "reviewer-a", "通过")
        backend.review.revoke_conclusion(con_revoked.conclusion_id, "reviewer-c", "作废")

        impact = backend.publish_revision("SCN-1", 1, make_content("v2"), "qa", "演进")
        self.assertEqual(impact.impacted_claim_ids, (issued.claim_id,))
        self.assertNotIn(draft.claim_id, impact.impacted_claim_ids)
        self.assertNotIn(revoked.claim_id, impact.impacted_claim_ids)
        self.assertIs(backend.claims.require_claim(revoked.claim_id).status, ClaimStatus.REVOKED)


class TraceabilityTests(unittest.TestCase):
    """双向追溯：能力 → 场景/运行/证据，以及证据/运行 → 能力。"""

    def setUp(self):
        self.backend = new_backend()
        self.claim, self.conclusion = issue_claim(self.backend, "CAP-1", "SCN-1")
        self.run_id = self.claim.run_ids[0]

    def test_capability_trace_reaches_runs_and_evidence(self):
        trace = self.backend.trace.capability_trace("CAP-1")

        self.assertEqual(trace["capability"]["required_scenarios"], ["SCN-1"])
        self.assertEqual(len(trace["claims"]), 1)
        claim_view = trace["claims"][0]
        self.assertEqual(claim_view["status"], "issued")
        self.assertEqual(claim_view["scenario_refs"], [{"scenario_id": "SCN-1", "version": 1}])
        self.assertEqual(claim_view["conclusion"]["conclusion_id"], self.conclusion.conclusion_id)

        run_view = claim_view["runs"][0]
        self.assertEqual(run_view["run_id"], self.run_id)
        self.assertEqual(run_view["software_version"], "sw-1.0")
        self.assertEqual(run_view["input_digest"], "input-1")
        self.assertEqual(run_view["evidence"][0]["digest"], "dg-ev-1")

    def test_run_trace_reaches_claims_and_capabilities(self):
        trace = self.backend.trace.run_trace(self.run_id)
        self.assertEqual(trace["run"]["run_id"], self.run_id)
        self.assertEqual(trace["scenario"]["version"], 1)
        self.assertEqual([c["claim_id"] for c in trace["claims"]], [self.claim.claim_id])
        self.assertEqual(trace["capabilities"], ["CAP-1"])
        self.assertEqual(trace["conclusions"][0]["conclusion_id"], self.conclusion.conclusion_id)

    def test_scenario_and_evidence_trace(self):
        scenario_trace = self.backend.trace.scenario_trace("SCN-1", 1)
        self.assertEqual([r["run_id"] for r in scenario_trace["runs"]], [self.run_id])
        self.assertEqual(scenario_trace["capabilities"], ["CAP-1"])

        evidence_trace = self.backend.trace.evidence_trace("dg-ev-1")
        self.assertEqual([r["run_id"] for r in evidence_trace["runs"]], [self.run_id])
        self.assertEqual([c["claim_id"] for c in evidence_trace["claims"]], [self.claim.claim_id])
        self.assertEqual(evidence_trace["capabilities"], ["CAP-1"])

        self.assertEqual(self.backend.trace.evidence_trace("dg-unknown")["runs"], [])


if __name__ == "__main__":
    unittest.main()
