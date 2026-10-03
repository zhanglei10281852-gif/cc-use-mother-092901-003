"""测试共用的构造助手。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from scenario_certification import (
    CertificationBackend,
    Evidence,
    ExpectedBehavior,
    FaultInjection,
    RoadEnvironment,
    RunOutcome,
    ScenarioContent,
    TrafficParticipant,
    VisibilityAndFriction,
)


def make_content(tag="base", *, layout="urban-junction", visibility_m=600.0, mu=0.8, fault="none", ttc=1.5):
    """构造一份五要素齐备的场景内容，tag 体现在道路特征与描述中。"""
    return ScenarioContent(
        road=RoadEnvironment(layout=layout, lane_count=2, speed_limit_kph=50.0, features=(tag,)),
        participants=(TrafficParticipant(kind="pedestrian", behavior="crossing", count=1),),
        environment=VisibilityAndFriction(condition="rain", visibility_m=visibility_m, friction_mu=mu),
        fault=FaultInjection(
            fault_type=fault,
            target="lidar" if fault != "none" else "",
            trigger_after_s=2.0 if fault != "none" else 0.0,
            duration_s=0.5 if fault != "none" else 0.0,
        ),
        expected=ExpectedBehavior(
            description=f"期望行为-{tag}",
            criteria=("no-collision", "comfort-brake"),
            thresholds={"min_ttc_s": ttc},
        ),
    )


def new_backend():
    return CertificationBackend()


def register_scenario(backend, scenario_id="SCN-1", tag="base", **kwargs):
    return backend.scenarios.register_scenario(scenario_id, make_content(tag, **kwargs), author="qa")


def add_run(backend, scenario_id, version, outcome=RunOutcome.PASSED, sw="sw-1.0", digest="input-1", ev="ev-1"):
    return backend.runs.record_run(
        scenario_id,
        version,
        sw,
        digest,
        outcome,
        evidence=(Evidence("log", f"s3://artifacts/{ev}", f"dg-{ev}"),),
    )


def issue_claim(backend, capability_id="CAP-1", scenario_id="SCN-1", version=1, reviewer="reviewer-a"):
    """注册能力（覆盖单个场景）、跑通一次通过运行、起草并签发声明。"""
    register_scenario(backend, scenario_id)
    backend.claims.register_capability(capability_id, f"能力-{capability_id}", (scenario_id,))
    run = add_run(backend, scenario_id, version).run
    claim = backend.claims.draft_claim(capability_id, (run.run_id,))
    conclusion = backend.review.submit_conclusion(claim.claim_id, "approve", reviewer, "通过")
    return backend.claims.require_claim(claim.claim_id), conclusion
