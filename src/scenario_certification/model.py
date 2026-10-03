"""场景核证领域模型。

设计要点：
- 场景内容由道路环境、交通参与者、能见度与路面附着、故障注入、期望行为五要素组成，
  内容规范化后的摘要即场景指纹，可复用、可比对。
- 场景版本（ScenarioRevision）不可变，通过 parent_version 指向上游版本，
  同一父版本可分出多支，形成版本树。
- 测试运行（RunRecord）不可变且按内容寻址：同一运行重复上传得到同一 run_id，
  重跑产生新记录而不是覆盖旧记录。
- 所有实体不依赖墙钟，排序一律使用存储分配的逻辑序号 seq，保证派生结果确定。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping

from .contracts import RunOutcome  # 复用既有契约中的运行结果枚举

__all__ = [
    "canonical_json",
    "content_digest",
    "RoadEnvironment",
    "TrafficParticipant",
    "VisibilityAndFriction",
    "FaultInjection",
    "ExpectedBehavior",
    "ScenarioContent",
    "ScenarioRef",
    "ScenarioRevision",
    "Evidence",
    "RunRecord",
    "Capability",
    "ClaimStatus",
    "CapabilityClaim",
    "Verdict",
    "ConclusionState",
    "ReviewConclusion",
    "BatchState",
    "ReleaseBatch",
    "RunOutcome",
]


def canonical_json(value: Any) -> str:
    """生成规范 JSON：键排序、无冗余空白，同一逻辑内容必得同一文本。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_digest(value: Any) -> str:
    """对可 JSON 序列化的内容计算 SHA-256 摘要。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


# ---------------------------------------------------------------------------
# 场景五要素
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoadEnvironment:
    """道路环境：路网类型、车道数、限速与道路特征。"""

    layout: str
    lane_count: int
    speed_limit_kph: float
    features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.layout:
            raise ValueError("道路环境必须给出路网类型")
        if self.lane_count < 1:
            raise ValueError("车道数必须为正整数")
        if self.speed_limit_kph < 0:
            raise ValueError("限速不能为负")
        object.__setattr__(self, "features", _sorted_unique(self.features))


@dataclass(frozen=True, order=True)
class TrafficParticipant:
    """交通参与者：类型、行为模式与数量。"""

    kind: str
    behavior: str
    count: int = 1

    def __post_init__(self) -> None:
        if not self.kind or not self.behavior:
            raise ValueError("交通参与者必须给出类型与行为")
        if self.count < 1:
            raise ValueError("交通参与者数量必须为正整数")


@dataclass(frozen=True)
class VisibilityAndFriction:
    """能见度与路面附着。"""

    condition: str
    visibility_m: float
    friction_mu: float

    def __post_init__(self) -> None:
        if not self.condition:
            raise ValueError("必须给出能见度工况")
        if self.visibility_m < 0:
            raise ValueError("能见度不能为负")
        if not 0 <= self.friction_mu <= 2.0:
            raise ValueError("路面附着系数超出合理范围 [0, 2]")


@dataclass(frozen=True)
class FaultInjection:
    """故障注入：故障类型、目标、触发时刻与持续时长。fault_type 为 none 表示不注入。"""

    fault_type: str = "none"
    target: str = ""
    trigger_after_s: float = 0.0
    duration_s: float = 0.0

    def __post_init__(self) -> None:
        if not self.fault_type:
            raise ValueError("故障类型不能为空，不注入时使用 none")
        if self.trigger_after_s < 0 or self.duration_s < 0:
            raise ValueError("故障触发时刻与持续时长不能为负")


@dataclass(frozen=True)
class ExpectedBehavior:
    """期望行为：文字描述、判定准则与量化阈值。

    thresholds 可传入 Mapping[str, float]，内部统一为按键排序的键值对元组，
    保证语义相同的内容得到相同的规范表示。
    """

    description: str
    criteria: tuple[str, ...]
    thresholds: tuple[tuple[str, float], ...] | Mapping[str, float] = ()

    def __post_init__(self) -> None:
        if not self.description:
            raise ValueError("期望行为必须给出描述")
        if not self.criteria:
            raise ValueError("期望行为至少包含一条判定准则")
        object.__setattr__(self, "criteria", _sorted_unique(tuple(self.criteria)))
        items = self.thresholds.items() if isinstance(self.thresholds, Mapping) else self.thresholds
        pairs = tuple(sorted((str(k), float(v)) for k, v in items))
        if len({k for k, _ in pairs}) != len(pairs):
            raise ValueError("判定阈值存在重复键")
        object.__setattr__(self, "thresholds", pairs)

    def threshold_map(self) -> dict[str, float]:
        return dict(self.thresholds)


@dataclass(frozen=True)
class ScenarioContent:
    """可复用的场景内容：五要素组合。digest 即场景指纹。"""

    road: RoadEnvironment
    participants: tuple[TrafficParticipant, ...]
    environment: VisibilityAndFriction
    fault: FaultInjection
    expected: ExpectedBehavior

    def __post_init__(self) -> None:
        if not self.participants:
            raise ValueError("场景至少包含一个交通参与者")
        object.__setattr__(self, "participants", tuple(sorted(self.participants)))

    def digest(self) -> str:
        return content_digest(asdict(self))


# ---------------------------------------------------------------------------
# 场景版本
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class ScenarioRef:
    """指向某场景某一版本的值对象。"""

    scenario_id: str
    version: int

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("场景版本必须为正整数")

    def label(self) -> str:
        return f"{self.scenario_id}:v{self.version}"


@dataclass(frozen=True)
class ScenarioRevision:
    """场景的不可变版本。parent_version 为 None 表示根版本。"""

    scenario_id: str
    version: int
    parent_version: int | None
    content: ScenarioContent
    content_hash: str
    author: str
    change_note: str
    seq: int

    @property
    def ref(self) -> ScenarioRef:
        return ScenarioRef(self.scenario_id, self.version)


# ---------------------------------------------------------------------------
# 测试运行与证据
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Evidence:
    """结果证据：制品类型、位置与内容摘要。"""

    kind: str
    uri: str
    digest: str

    def __post_init__(self) -> None:
        if not self.kind or not self.uri or not self.digest:
            raise ValueError("证据必须给出类型、位置与内容摘要")


@dataclass(frozen=True)
class RunRecord:
    """一次测试运行的不可变记录。

    run_id 由运行内容寻址生成：重复上传同一运行得到同一 run_id，
    重跑（输入相同、结果或证据不同）生成新记录并递增 attempt，原始记录保持不变。
    """

    run_id: str
    scenario_id: str
    scenario_version: int
    software_version: str
    input_digest: str
    outcome: RunOutcome
    evidence: tuple[Evidence, ...]
    attempt: int
    external_id: str | None
    seq: int

    @property
    def ref(self) -> ScenarioRef:
        return ScenarioRef(self.scenario_id, self.scenario_version)


# ---------------------------------------------------------------------------
# 能力声明
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    """待核证能力：必须覆盖的场景集合（按场景标识）。"""

    capability_id: str
    name: str
    required_scenarios: tuple[str, ...]
    seq: int


class ClaimStatus(StrEnum):
    DRAFT = "draft"  # 草稿
    ISSUED = "issued"  # 已签发
    SUSPENDED = "suspended"  # 局部场景被退回，待补证
    REVOKED = "revoked"  # 已撤销（终态）


@dataclass(frozen=True)
class CapabilityClaim:
    """能力声明：某能力在一组场景版本上由若干通过运行支撑，经评审后签发。"""

    claim_id: str
    capability_id: str
    scenario_refs: tuple[ScenarioRef, ...]
    run_ids: tuple[str, ...]
    status: ClaimStatus
    returned_refs: tuple[ScenarioRef, ...]
    conclusion_id: str | None
    batch_id: str | None
    suspended_seq: int | None
    seq: int


# ---------------------------------------------------------------------------
# 评审与发布批次
# ---------------------------------------------------------------------------


class Verdict(StrEnum):
    APPROVE = "approve"
    RETURN = "return"


class ConclusionState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class ReviewConclusion:
    """评审结论：针对一份能力声明的评审决定，可被撤销。"""

    conclusion_id: str
    claim_id: str
    verdict: Verdict
    reviewer: str
    rationale: str
    state: ConclusionState
    seq: int


class BatchState(StrEnum):
    OPEN = "open"
    FROZEN = "frozen"


@dataclass(frozen=True)
class ReleaseBatch:
    """发布批次：一组已签发声明的发布单元，冻结后成员不可变更。"""

    batch_id: str
    claim_ids: tuple[str, ...]
    state: BatchState
    seq: int
