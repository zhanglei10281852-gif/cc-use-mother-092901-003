import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from scenario_certification import ClaimStatus, RunOutcome, ScenarioRef
from support import add_run, make_content, new_backend, register_scenario


def full_script():
    """完整业务剧本：版本分叉、重复上传、失败重跑、签发、冻结、退回、补证、撤销。"""
    backend = new_backend()

    # 场景注册与版本分叉
    register_scenario(backend, "SCN-1", tag="urban-v1")
    register_scenario(backend, "SCN-2", tag="highway-v1")
    backend.publish_revision("SCN-1", 1, make_content("urban-v2"), "qa", "主分支演进")
    backend.publish_revision("SCN-1", 1, make_content("urban-v2-fork", fault="sensor-dropout"), "qa", "故障分叉")

    # 运行收录：通过、重复上传、失败重跑
    add_run(backend, "SCN-1", 1, RunOutcome.PASSED, ev="ev-1")
    add_run(backend, "SCN-1", 1, RunOutcome.PASSED, ev="ev-1")  # 重复上传，应去重
    add_run(backend, "SCN-1", 1, RunOutcome.FAILED, ev="ev-rerun")  # 失败重跑，不覆盖
    add_run(backend, "SCN-2", 1, RunOutcome.PASSED, ev="ev-2")

    # 能力、声明、签发
    backend.claims.register_capability("CAP-1", "领航辅助", ("SCN-1", "SCN-2"))
    run_ids = [r for r in backend.store.runs if backend.store.runs[r].outcome is RunOutcome.PASSED]
    claim = backend.claims.draft_claim("CAP-1", tuple(sorted(run_ids)))
    conclusion = backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-a", "覆盖充分")

    # 批次冻结、局部退回、补证重签
    batch = backend.review.create_batch((claim.claim_id,))
    backend.review.freeze_batch(batch.batch_id)
    backend.review.return_scenarios(
        batch.batch_id, claim.claim_id, (ScenarioRef("SCN-2", 1),), "reviewer-b", "高速场景存疑"
    )
    fix = add_run(backend, "SCN-2", 1, RunOutcome.PASSED, digest="input-fix", ev="ev-fix").run
    backend.claims.attach_runs(claim.claim_id, (fix.run_id,))
    backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-a", "补证通过")

    # 撤销最初的签发结论（已被取代，不影响声明）
    backend.review.revoke_conclusion(conclusion.conclusion_id, "reviewer-c", "旧结论作废")
    return backend


class DeterminismTests(unittest.TestCase):
    """同一操作序列必然派生同一状态：版本分叉、覆盖去重、撤销传播均确定。"""

    def test_replaying_same_script_yields_identical_snapshot(self):
        first, second = full_script(), full_script()
        self.assertEqual(first.snapshot(), second.snapshot())

    def test_snapshot_is_canonical_and_events_strictly_ordered(self):
        backend = full_script()
        snapshot = backend.snapshot()
        self.assertEqual(snapshot, json.dumps(json.loads(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        seqs = [e.seq for e in backend.store.events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_duplicate_upload_changes_nothing_but_event_log(self):
        backend_a = new_backend()
        register_scenario(backend_a, "SCN-1")
        backend_a.claims.register_capability("CAP-1", "自动紧急制动", ("SCN-1",))
        add_run(backend_a, "SCN-1", 1)

        backend_b = new_backend()
        register_scenario(backend_b, "SCN-1")
        backend_b.claims.register_capability("CAP-1", "自动紧急制动", ("SCN-1",))
        add_run(backend_b, "SCN-1", 1)
        add_run(backend_b, "SCN-1", 1)  # 重复上传
        add_run(backend_b, "SCN-1", 1)  # 再次重复

        self.assertEqual(set(backend_a.store.runs), set(backend_b.store.runs))
        report_a = backend_a.claims.coverage_report("CAP-1")
        report_b = backend_b.claims.coverage_report("CAP-1")
        self.assertEqual(report_a, report_b)

    def test_revocation_propagation_is_deterministic(self):
        def revoke_script():
            backend = new_backend()
            register_scenario(backend, "SCN-1")
            backend.claims.register_capability("CAP-1", "自动紧急制动", ("SCN-1",))
            run = add_run(backend, "SCN-1", 1).run
            claim = backend.claims.draft_claim("CAP-1", (run.run_id,))
            conclusion = backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-a", "通过")
            backend.review.revoke_conclusion(conclusion.conclusion_id, "reviewer-c", "依据失效")
            return backend

        a, b = revoke_script(), revoke_script()
        self.assertEqual(a.snapshot(), b.snapshot())
        claim = next(iter(a.store.claims.values()))
        self.assertIs(claim.status, ClaimStatus.REVOKED)
        events = [e.kind for e in a.store.events]
        self.assertLess(events.index("conclusion_revoked"), events.index("claim_revoked"))


if __name__ == "__main__":
    unittest.main()
