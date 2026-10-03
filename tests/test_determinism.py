"""确定性：版本分叉、覆盖去重、撤销传播在重放后状态完全一致。"""

import json
import unittest

from support import accept, ambient, define_scenario, expected, make_run

from scenario_certification import ScenarioCertificationBackend


def run_full_script():
    """一段覆盖三大关注点的完整操作序列。"""
    backend = ScenarioCertificationBackend()
    define_scenario(backend, "SC-1")
    define_scenario(backend, "SC-2")

    # 覆盖去重：同一运行上传三次
    for _ in range(3):
        backend.record_run(make_run("R-1", "SC-1", 1))
    backend.record_run(make_run("R-2", "SC-2", 1))
    accept(backend, "R-1")
    accept(backend, "R-2")

    backend.create_batch("REL-1", "三季度发布", at="2026-09-04T09:00:00Z")
    backend.issue_claim("CLM-1", "aeb-urban", "sw-1.0",
                        pins=[("SC-1", 1), ("SC-2", 1)], batch_id="REL-1",
                        at="2026-09-05T09:00:00Z", by="reviewer-li")

    # 版本分叉：先改阈值，再从 v1 分叉出雾天分支
    backend.fork_scenario("SC-1", reason="阈值收紧", at="2026-09-10T09:00:00Z",
                       by="team", expected=(expected(threshold=2.0),))
    backend.fork_scenario("SC-1", reason="雾天分支", base_version=1,
                          at="2026-09-11T09:00:00Z", by="team",
                          ambient=ambient(weather="fog", visibility_m=30.0))

    # 撤销传播
    backend.revoke_verdict("V-R-2", reason="证据存疑",
                           at="2026-09-12T09:00:00Z", by="reviewer-wang")
    backend.freeze_batch("REL-1", at="2026-09-13T09:00:00Z", by="reviewer-wang")
    return backend


class DeterminismTests(unittest.TestCase):
    def test_replay_produces_identical_snapshot(self):
        first = run_full_script()
        second = run_full_script()
        self.assertEqual(
            json.dumps(first.snapshot(), sort_keys=True, ensure_ascii=False),
            json.dumps(second.snapshot(), sort_keys=True, ensure_ascii=False))

    def test_event_log_is_identical_across_replays(self):
        first, second = run_full_script(), run_full_script()
        self.assertEqual(first.events(), second.events())

    def test_coverage_dedup_is_stable_under_repeated_uploads(self):
        backend = run_full_script()
        before = backend.coverage_report("CLM-1")
        backend.record_run(make_run("R-1", "SC-1", 1))  # 再次重复上传
        after = backend.coverage_report("CLM-1")
        self.assertEqual(before.pins[0].qualifying_runs, ("R-1",))
        self.assertEqual(before, after)

    def test_fork_lineage_is_deterministic(self):
        backend = run_full_script()
        self.assertEqual(backend.list_versions("SC-1"), (1, 2, 3))
        self.assertEqual(backend.get_scenario("SC-1", 2).parent_version, 1)
        self.assertEqual(backend.get_scenario("SC-1", 3).parent_version, 1)
        # v1 内容未被任何分叉修改
        self.assertEqual(backend.get_scenario("SC-1", 1).expected[0].threshold, 1.5)


if __name__ == "__main__":
    unittest.main()
