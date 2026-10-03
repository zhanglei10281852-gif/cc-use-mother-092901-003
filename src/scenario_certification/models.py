"""场景核证领域模型。

在 contracts.py 的基础契约之上提供完整模型：

- 版本化场景定义：道路环境、交通参与者、能见度与路面附着、
  故障注入、期望行为五个方面的不可变组合；
- 测试运行记录：软件版本、输入摘要、结果证据，只增不改；
- 评审结论、能力声明与发布批次：支撑签发、退回与撤销。

所有实体为冻结数据类；任何变更通过新版本或新记录表达，
从而保证旧报告永远指向它被验证时的那个场景版本。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Iterable, Mapping

from .contracts import RunOutcome

__all__ = [
    "AmbientConditions",
    "BatchStatus",
    "CapabilityClaim",
    "ClaimStatus",
    "CoverageReport",
    "Event",
    "Evidence",
    "ExpectedBehavior",
    "FaultInjection",
    "ForkResult",
    "ImpactReport",
    "PinCoverage",
    "ReleaseBatch",
    "ReviewVerdict",
    "RoadEnvironment",
    "ScenarioDefinition",
    "ScenarioPin",
    "TestRunRecord",
    "TrafficParticipant",
    "VerdictDecision",
    "digest_payload",
]


def _check_hex_digest(value: str, field: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise ValueError(f"{field}必须是 64 位十六进制摘要")


def digest_payload(payload: object) -> str:
    """对输入数据计算确定性摘要（规范化 JSON + SHA-256）。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 场景定义的五个方面
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoadEnvironment:
    """道路环境：道路类型、车道数、线形与限速。"""

    road_type: str            # urban / expressway / rural ...
    lane_count: int
    geometry: str             # straight / curve / junction / ramp ...
    speed_limit_kph: float

    def __post_init__(self) -> None:
        if self.lane_count < 1:
            raise ValueError("车道数必须为正整数")
        if self.speed_limit_kph <= 0:
            raise ValueError("限速必须为正值")


@dataclass(frozen=True)
class TrafficParticipant:
    """交通参与者及其行为剧本。"""

    participant_id: str
    kind: str                 # ego / vehicle / pedestrian / cyclist ...
    behavior: str             # approach / cut_in / crossing / emergency_brake ...
    initial_speed_mps: float

    def __post_init__(self) -> None:
        if not self.participant_id:
            raise ValueError("参与者标识不能为空")
        if self.initial_speed_mps < 0:
            raise ValueError("初速度不能为负")


@dataclass(frozen=True)
class AmbientConditions:
    """能见度与路面附着条件。"""

    visibility_m: float
    friction_coeff: float
    weather: str              # clear / rain / fog / snow ...
    illumination: str         # day / night / dusk ...

    def __post_init__(self) -> None:
        if self.visibility_m <= 0:
            raise ValueError("能见度必须为正值")
        if not 0 < self.friction_coeff <= 1.1:
            raise ValueError("路面附着系数超出物理范围")


@dataclass(frozen=True)
class FaultInjection:
    """故障注入：目标、类型与时间窗。"""

    target: str               # 例如 sensor:front_camera / actuator:brake
    fault_type: str           # dropout / noise / delay / stuck ...
    start_s: float
    duration_s: float

    def __post_init__(self) -> None:
        if self.start_s < 0:
            raise ValueError("故障起始时间不能为负")
        if self.duration_s <= 0:
            raise ValueError("故障持续时间必须为正值")


@dataclass(frozen=True)
class ExpectedBehavior:
    """期望行为：判定指标、比较方向与阈值。"""

    metric: str               # min_ttc_s / collision_count / max_lane_deviation_m ...
    comparator: str           # >= / <= / ==
    threshold: float
    description: str = ""

    _COMPARATORS = (">=", "<=", "==")

    def __post_init__(self) -> None:
        if self.comparator not in self._COMPARATORS:
            raise ValueError(f"不支持的比较符: {self.comparator}")


# ---------------------------------------------------------------------------
# 版本化场景定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioDefinition:
    """一个场景版本的完整定义，创建后不可变。

    版本号在同一 scenario_id 下连续递增；parent_version 记录分叉来源，
    允许从任意历史版本分叉，形成版本谱系。
    """

    scenario_id: str
    version: int
    title: str
    road: RoadEnvironment
    participants: tuple[TrafficParticipant, ...]
    ambient: AmbientConditions
    faults: tuple[FaultInjection, ...]
    expected: tuple[ExpectedBehavior, ...]
    parameters: tuple[tuple[str, str], ...]
    parent_version: int | None
    change_reason: str
    created_at: str
    created_by: str

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("场景标识不能为空")
        if self.version < 1:
            raise ValueError("场景版本必须为正整数")
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError("父版本必须小于当前版本")
        # 集合类字段排序规范化，保证内容摘要与输入顺序无关
        object.__setattr__(self, "participants",
                           tuple(sorted(self.participants, key=lambda p: p.participant_id)))
        object.__setattr__(self, "faults",
                           tuple(sorted(self.faults, key=lambda f: (f.target, f.start_s))))
        object.__setattr__(self, "expected",
                           tuple(sorted(self.expected, key=lambda e: e.metric)))
        object.__setattr__(self, "parameters", tuple(sorted(self.parameters)))

    def canonical(self) -> dict:
        """内容摘要的规范化视图，不含标识、版本与元数据。

        两个团队用不同名字定义了相同内容的场景时，内容摘要相同，
        可以据此发现重复命名。
        """
        return {
            "title": self.title,
            "road": asdict(self.road),
            "participants": [asdict(p) for p in self.participants],
            "ambient": asdict(self.ambient),
            "faults": [asdict(f) for f in self.faults],
            "expected": [asdict(e) for e in self.expected],
            "parameters": [list(p) for p in self.parameters],
        }

    def content_digest(self) -> str:
        return digest_payload(self.canonical())


# ---------------------------------------------------------------------------
# 测试运行与证据
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """一条结果证据（日志、视频、指标报告等）的内容寻址引用。"""

    evidence_id: str
    kind: str                 # log / video / metrics ...
    uri: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.evidence_id:
            raise ValueError("证据标识不能为空")
        _check_hex_digest(self.sha256, "证据摘要")


@dataclass(frozen=True)
class TestRunRecord:
    """一次测试运行的不可变记录。

    运行记录只增不改：失败后的重跑是新的 run_id，并通过 rerun_of
    指向原始运行，绝不覆盖原始结果。
    """

    run_id: str
    scenario_id: str
    scenario_version: int
    software_version: str
    input_digest: str
    outcome: RunOutcome
    evidence: tuple[Evidence, ...]
    executed_at: str
    recorded_at: str
    recorded_by: str
    rerun_of: str | None = None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("运行标识不能为空")
        if self.scenario_version < 1:
            raise ValueError("场景版本必须为正整数")
        _check_hex_digest(self.input_digest, "输入摘要")
        evidence = tuple(sorted(self.evidence, key=lambda e: e.evidence_id))
        ids = [e.evidence_id for e in evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("同一运行内证据标识必须唯一")
        object.__setattr__(self, "evidence", evidence)


# ---------------------------------------------------------------------------
# 评审结论
# ---------------------------------------------------------------------------


class VerdictDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ReviewVerdict:
    """评审人员对一次运行给出的结论；同一运行同一时间只有一条有效结论。"""

    verdict_id: str
    run_id: str
    decision: VerdictDecision
    reviewer: str
    rationale: str
    decided_at: str

    def __post_init__(self) -> None:
        if not self.verdict_id:
            raise ValueError("结论标识不能为空")


# ---------------------------------------------------------------------------
# 能力声明
# ---------------------------------------------------------------------------


class ClaimStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    AFFECTED = "affected"      # 场景变更或证据撤销，需要重新核证
    RETURNED = "returned"      # 评审退回局部场景
    SUPERSEDED = "superseded"  # 已被重新签发的声明取代
    REVOKED = "revoked"        # 结论被撤销


@dataclass(frozen=True)
class ScenarioPin:
    """能力声明钉住的精确场景版本。"""

    scenario_id: str
    version: int

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("场景版本必须为正整数")


@dataclass(frozen=True)
class CapabilityClaim:
    """一项能力在指定软件版本、指定场景版本集合上获得验证的声明。"""

    claim_id: str
    capability: str
    software_version: str
    pins: tuple[ScenarioPin, ...]
    status: ClaimStatus
    batch_id: str | None = None
    issued_at: str | None = None
    issued_by: str | None = None
    supersedes: str | None = None
    returned_scenarios: tuple[str, ...] = ()
    status_reason: str = ""

    def __post_init__(self) -> None:
        if not self.claim_id:
            raise ValueError("声明标识不能为空")
        object.__setattr__(
            self, "pins",
            tuple(sorted(self.pins, key=lambda p: (p.scenario_id, p.version))))
        object.__setattr__(self, "returned_scenarios",
                           tuple(sorted(self.returned_scenarios)))


# ---------------------------------------------------------------------------
# 发布批次
# ---------------------------------------------------------------------------


class BatchStatus(StrEnum):
    OPEN = "open"
    FROZEN = "frozen"


@dataclass(frozen=True)
class ReleaseBatch:
    """一个发布批次：冻结后不能再加入声明或退回场景。"""

    batch_id: str
    title: str
    status: BatchStatus
    created_at: str
    frozen_at: str | None = None
    frozen_by: str | None = None

    def __post_init__(self) -> None:
        if not self.batch_id:
            raise ValueError("批次标识不能为空")


# ---------------------------------------------------------------------------
# 事件与报告
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """追加式审计事件；seq 单调递增，detail 按键排序。"""

    seq: int
    kind: str
    subject: str
    detail: tuple[tuple[str, str], ...]
    at: str


@dataclass(frozen=True)
class ImpactReport:
    """场景变更对已签发能力声明的影响。"""

    scenario_id: str
    new_version: int
    affected_claims: tuple[str, ...]


@dataclass(frozen=True)
class ForkResult:
    definition: ScenarioDefinition
    impact: ImpactReport


@dataclass(frozen=True)
class PinCoverage:
    pin: ScenarioPin
    qualifying_runs: tuple[str, ...]
    covered: bool


@dataclass(frozen=True)
class CoverageReport:
    claim_id: str
    pins: tuple[PinCoverage, ...]
    covered: int
    total: int
    complete: bool


def normalize_parameters(
    parameters: Mapping[str, str] | Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """把映射或键值对序列规范化为排序后的元组。"""
    items = parameters.items() if isinstance(parameters, Mapping) else parameters
    return tuple(sorted((str(k), str(v)) for k, v in items))
