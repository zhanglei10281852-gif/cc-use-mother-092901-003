"""场景核证后端端到端冒烟演示。

流程：定义场景 → 录入运行与证据 → 评审接受 → 签发能力声明 →
场景阈值调整（分叉新版本）→ 输出受影响声明与双向追溯。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

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

backend = ScenarioCertificationBackend()

# 1. 定义场景 v1：城市路口行人横穿（雨、黄昏、低附着）
backend.create_scenario(
    "SC-PED-CROSS", "城市路口行人横穿",
    road=RoadEnvironment("urban", 2, "junction", 50.0),
    participants=(
        TrafficParticipant("ego", "vehicle", "approach", 13.9),
        TrafficParticipant("ped-1", "pedestrian", "crossing", 1.2),
    ),
    ambient=AmbientConditions(80.0, 0.7, "rain", "dusk"),
    faults=(),
    expected=(ExpectedBehavior("min_ttc_s", ">=", 1.5, "最小碰撞时间不低于 1.5 秒"),),
    parameters={"odc": "city-night-rain"},
    reason="初始版本", at="2026-09-01T09:00:00Z", by="scenario-team")

# 2. 录入运行与证据（重复上传会被幂等去重）
for _ in range(2):
    backend.record_run(TestRunRecord(
        run_id="RUN-1001", scenario_id="SC-PED-CROSS", scenario_version=1,
        software_version="sw-2026.09",
        input_digest=digest_payload({"seed": 42, "map": "city-01"}),
        outcome=RunOutcome.PASSED,
        evidence=(Evidence("EV-1001", "metrics",
                           "s3://evidence/RUN-1001/metrics.json", "a" * 64),),
        executed_at="2026-09-02T10:00:00Z", recorded_at="2026-09-02T12:00:00Z",
        recorded_by="ci"))

# 3. 评审接受运行结论
backend.submit_verdict(ReviewVerdict(
    "V-1001", "RUN-1001", VerdictDecision.ACCEPTED, "reviewer-li",
    "指标达标，证据完整", "2026-09-03T09:00:00Z"))

# 4. 在发布批次内签发能力声明
backend.create_batch("REL-2026Q3", "三季度发布", at="2026-09-04T09:00:00Z")
backend.issue_claim("CLM-AEB-001", "aeb-pedestrian-urban", "sw-2026.09",
                    pins=[("SC-PED-CROSS", 1)], batch_id="REL-2026Q3",
                    at="2026-09-05T09:00:00Z", by="reviewer-li")

# 5. 阈值收紧 → 场景分叉 v2，已签发声明被标记受影响
fork = backend.fork_scenario(
    "SC-PED-CROSS", reason="最小碰撞时间阈值收紧到 2.0 秒",
    at="2026-09-10T09:00:00Z", by="scenario-team",
    expected=(ExpectedBehavior("min_ttc_s", ">=", 2.0, "阈值收紧"),))

print(json.dumps({
    "scenario": fork.definition.scenario_id,
    "new_version": fork.definition.version,
    "affected_claims": list(fork.impact.affected_claims),
    "claim_trace": backend.trace_claim("CLM-AEB-001"),
}, ensure_ascii=False, indent=2))
