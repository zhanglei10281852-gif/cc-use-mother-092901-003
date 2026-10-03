"""场景核证后端端到端冒烟：注册场景 → 收录运行 → 签发声明 → 冻结批次 → 变更影响 → 双向追溯。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

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


def build_content(tag, ttc):
    return ScenarioContent(
        road=RoadEnvironment(layout="urban-junction", lane_count=2, speed_limit_kph=50.0, features=(tag,)),
        participants=(TrafficParticipant(kind="pedestrian", behavior="crossing", count=1),),
        environment=VisibilityAndFriction(condition="rain", visibility_m=400.0, friction_mu=0.6),
        fault=FaultInjection(fault_type="none"),
        expected=ExpectedBehavior(
            description="行人横穿时车辆应在舒适制动下避免碰撞",
            criteria=("no-collision", "comfort-brake"),
            thresholds={"min_ttc_s": ttc},
        ),
    )


def main():
    backend = CertificationBackend()

    backend.scenarios.register_scenario("SCN-AEB-001", build_content("v1", 1.5), author="scenario-team")
    run = backend.runs.record_run(
        "SCN-AEB-001", 1, "sw-2026.10", "input-sha256-abc", RunOutcome.PASSED,
        evidence=(Evidence("metric-report", "s3://evidence/run-1", "dg-001"),),
    )
    duplicate = backend.runs.record_run(
        "SCN-AEB-001", 1, "sw-2026.10", "input-sha256-abc", RunOutcome.PASSED,
        evidence=(Evidence("metric-report", "s3://evidence/run-1", "dg-001"),),
    )

    backend.claims.register_capability("CAP-AEB", "行人横穿自动紧急制动", ("SCN-AEB-001",))
    claim = backend.claims.draft_claim("CAP-AEB", (run.run.run_id,))
    backend.review.submit_conclusion(claim.claim_id, "approve", "reviewer-li", "证据充分")
    batch = backend.review.create_batch((claim.claim_id,))
    batch = backend.review.freeze_batch(batch.batch_id)

    impact = backend.publish_revision(
        "SCN-AEB-001", 1, build_content("v2", 2.0), "scenario-team", "收紧最小碰撞时间阈值"
    )
    trace = backend.trace.capability_trace("CAP-AEB")
    coverage = backend.claims.coverage_report("CAP-AEB")

    print(json.dumps({
        "run_id": run.run.run_id,
        "duplicate_created": duplicate.created,
        "batch": {"id": batch.batch_id, "state": batch.state.value},
        "new_revision": impact.revision.version,
        "impacted_claims": list(impact.impacted_claim_ids),
        "coverage": {"covered": coverage.covered_count, "complete": coverage.complete},
        "trace_claim_status": trace["claims"][0]["status"],
        "trace_evidence_digest": trace["claims"][0]["runs"][0]["evidence"][0]["digest"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
