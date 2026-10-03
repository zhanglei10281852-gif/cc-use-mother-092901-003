"""版本分叉：场景变更产生新版本，旧版本与旧运行原样保留。"""

import unittest

from support import (
    T0,
    accept,
    ambient,
    define_scenario,
    expected,
    fresh_backend_with_claim,
    make_run,
    participant,
    road,
)

from scenario_certification import (
    ClaimStatus,
    ConflictError,
    ScenarioCertificationBackend,
    ScenarioDefinition,
    StateError,
)


class ScenarioForkTests(unittest.TestCase):
    def test_fork_creates_new_version_and_keeps_base_intact(self):
        backend = ScenarioCertificationBackend()
        v1 = define_scenario(backend, "SC-1")
        digest_before = v1.content_digest()

        result = backend.fork_scenario(
            "SC-1", reason="阈值收紧", at="2026-09-10T09:00:00Z", by="scenario-team",
            expected=(expected(threshold=2.0),))

        self.assertEqual(result.definition.version, 2)
        self.assertEqual(result.definition.parent_version, 1)
        # 旧版本原样保留
        self.assertEqual(backend.get_scenario("SC-1", 1).content_digest(), digest_before)
        self.assertEqual(backend.get_scenario("SC-1", 1).expected[0].threshold, 1.5)
        self.assertEqual(backend.get_scenario("SC-1").version, 2)

    def test_fork_from_historical_version_branches_lineage(self):
        backend = ScenarioCertificationBackend()
        define_scenario(backend, "SC-1")
        backend.fork_scenario("SC-1", reason="阈值收紧", at="2026-09-10T09:00:00Z",
                              by="team", expected=(expected(threshold=2.0),))
        # 从 v1 分叉出 v3：版本号线性递增，父指针形成谱系
        result = backend.fork_scenario(
            "SC-1", reason="改用雾天条件", at="2026-09-11T09:00:00Z", by="team",
            base_version=1, ambient=ambient(weather="fog", visibility_m=30.0))

        self.assertEqual(result.definition.version, 3)
        self.assertEqual(result.definition.parent_version, 1)
        self.assertEqual(backend.list_versions("SC-1"), (1, 2, 3))
        # v2 不受 v3 影响
        self.assertEqual(backend.get_scenario("SC-1", 2).expected[0].threshold, 2.0)
        self.assertEqual(backend.get_scenario("SC-1", 2).parent_version, 1)

    def test_runs_stay_bound_to_original_version_after_fork(self):
        backend = ScenarioCertificationBackend()
        define_scenario(backend, "SC-1")
        backend.record_run(make_run("R-1", "SC-1", 1))
        backend.fork_scenario("SC-1", reason="阈值收紧", at="2026-09-10T09:00:00Z",
                              by="team", expected=(expected(threshold=2.0),))

        trace = backend.trace_scenario("SC-1", 1)
        self.assertEqual(trace["versions"][0]["runs"], ["R-1"])
        self.assertEqual(backend.trace_scenario("SC-1", 2)["versions"][0]["runs"], [])

    def test_content_digest_reveals_same_content_under_different_names(self):
        backend = ScenarioCertificationBackend()
        define_scenario(backend, "SC-1")
        # 另一团队用不同名字定义了相同内容 → 内容摘要一致，可发现重复命名
        backend.create_scenario(
            "SC-RENAMED", "城市路口行人横穿",
            road=road(), participants=(participant(),), ambient=ambient(),
            faults=(), expected=(expected(),),
            parameters=(("odc", "city-night-rain"),),
            reason="初始版本", at=T0, by="team-b")
        self.assertEqual(backend.get_scenario("SC-1", 1).content_digest(),
                         backend.get_scenario("SC-RENAMED", 1).content_digest())

    def test_duplicate_create_rejected(self):
        backend = ScenarioCertificationBackend()
        define_scenario(backend, "SC-1")
        with self.assertRaises(ConflictError):
            define_scenario(backend, "SC-1")

    def test_fork_marks_issued_claims_affected_deterministically(self):
        backend = fresh_backend_with_claim()
        # 另一项声明只钉住 SC-2，不应受 SC-1 变更影响
        backend.record_run(make_run("R-3", "SC-2", 1, evidence_ids=("EV-3",)))
        accept(backend, "R-3")
        backend.issue_claim("CLM-2", "lka-urban", "sw-1.0", pins=[("SC-2", 1)],
                            at="2026-09-06T09:00:00Z", by="reviewer-li")

        result = backend.fork_scenario(
            "SC-1", reason="阈值收紧", at="2026-09-10T09:00:00Z", by="team",
            expected=(expected(threshold=2.0),))

        self.assertEqual(result.impact.affected_claims, ("CLM-1",))
        self.assertEqual(backend.get_claim("CLM-1").status, ClaimStatus.AFFECTED)
        self.assertEqual(backend.get_claim("CLM-2").status, ClaimStatus.ISSUED)

    def test_registering_non_sequential_version_rejected(self):
        backend = ScenarioCertificationBackend()
        define_scenario(backend, "SC-1")
        v1 = backend.get_scenario("SC-1", 1)
        with self.assertRaises(StateError):
            backend._register(ScenarioDefinition(
                scenario_id="SC-1", version=3, title=v1.title, road=v1.road,
                participants=v1.participants, ambient=v1.ambient, faults=v1.faults,
                expected=v1.expected, parameters=v1.parameters,
                parent_version=1, change_reason="跳版本", created_at=T0,
                created_by="team"))


if __name__ == "__main__":
    unittest.main()
