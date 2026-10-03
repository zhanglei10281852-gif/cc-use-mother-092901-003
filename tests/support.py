"""测试公共构造器：提供确定性的场景、运行与证据默认值。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from scenario_certification import (
    AmbientConditions,
    Evidence,
    ExpectedBehavior,
    ReviewVerdict,
    RoadEnvironment,
    RunOutcome,
    ScenarioCertificationBackend,
    TestRunRecord,
    TrafficParticipant,
    VerdictDecision,
    digest_payload,
)

T0 = "2026-09-01T09:00:00Z"


def road(**kw):
    defaults = dict(road_type="urban", lane_count=2, geometry="junction",
                    speed_limit_kph=50.0)
    return RoadEnvironment(**(defaults | kw))


def ambient(**kw):
    defaults = dict(visibility_m=80.0, friction_coeff=0.7,
                    weather="rain", illumination="dusk")
    return AmbientConditions(**(defaults | kw))


def participant(pid="ped-1", **kw):
    defaults = dict(kind="pedestrian", behavior="crossing", initial_speed_mps=1.2)
    return TrafficParticipant(pid, **(defaults | kw))


def expected(metric="min_ttc_s", threshold=1.5, **kw):
    defaults = dict(comparator=">=", description="最小碰撞时间阈值")
    return ExpectedBehavior(metric, threshold=threshold, **(defaults | kw))


def evidence(eid, seed="a"):
    return Evidence(eid, "metrics", f"s3://evidence/{eid}.json", seed * 64)


def define_scenario(backend, scenario_id="SC-1", *, at=T0, **kw):
    defaults = dict(
        title="城市路口行人横穿",
        road=road(),
        participants=(participant(),),
        ambient=ambient(),
        faults=(),
        expected=(expected(),),
        parameters={"odc": "city-night-rain"},
        reason="初始版本",
        by="scenario-team",
    )
    return backend.create_scenario(scenario_id, defaults.pop("title"),
                                   **(defaults | kw), at=at)


def make_run(run_id, scenario_id="SC-1", version=1, *,
             software="sw-1.0", outcome=RunOutcome.PASSED,
             evidence_ids=("EV-1",), at="2026-09-02T10:00:00Z",
             rerun_of=None, seed="b"):
    return TestRunRecord(
        run_id=run_id, scenario_id=scenario_id, scenario_version=version,
        software_version=software,
        input_digest=digest_payload({"run": run_id, "seed": seed}),
        outcome=outcome,
        evidence=tuple(evidence(e, seed=seed) for e in evidence_ids),
        executed_at=at, recorded_at=at, recorded_by="ci",
        rerun_of=rerun_of)


def accept(backend, run_id, verdict_id=None, *, at="2026-09-03T09:00:00Z",
           reviewer="reviewer-li"):
    verdict_id = verdict_id or f"V-{run_id}"
    return backend.submit_verdict(ReviewVerdict(
        verdict_id, run_id, VerdictDecision.ACCEPTED, reviewer, "指标达标", at))


def fresh_backend_with_claim():
    """构造：两个场景各 v1，各有一条已接受的通过运行，签发一项声明。"""
    backend = ScenarioCertificationBackend()
    define_scenario(backend, "SC-1")
    define_scenario(backend, "SC-2")
    backend.record_run(make_run("R-1", "SC-1", 1))
    backend.record_run(make_run("R-2", "SC-2", 1))
    accept(backend, "R-1")
    accept(backend, "R-2")
    backend.issue_claim("CLM-1", "aeb-urban", "sw-1.0",
                        pins=[("SC-1", 1), ("SC-2", 1)],
                        at="2026-09-05T09:00:00Z", by="reviewer-li")
    return backend
