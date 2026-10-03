"""仅追加的内存存储。

全局逻辑序号（seq）充当逻辑时钟：所有实体的创建与所有状态迁移都按序号排序，
不依赖墙钟与随机源，因此同一操作序列必然派生出同一状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .model import (
    Capability,
    CapabilityClaim,
    ReleaseBatch,
    ReviewConclusion,
    RunRecord,
    ScenarioRevision,
    canonical_json,
)


@dataclass(frozen=True)
class Event:
    """状态迁移事件，追加即不可改。"""

    seq: int
    kind: str
    payload: str  # 规范 JSON 文本


class Store:
    """领域状态的单一事实来源。实体表可被服务层就地替换（不可变对象的替换式更新），
    事件表只追加，用于审计与确定性校验。"""

    def __init__(self) -> None:
        self._seq = 0
        self._counters: dict[str, int] = {}
        self.scenarios: dict[str, dict[int, ScenarioRevision]] = {}
        self.capabilities: dict[str, Capability] = {}
        self.runs: dict[str, RunRecord] = {}
        self.claims: dict[str, CapabilityClaim] = {}
        self.conclusions: dict[str, ReviewConclusion] = {}
        self.batches: dict[str, ReleaseBatch] = {}
        self.events: list[Event] = []

    def tick(self) -> int:
        """分配下一个全局逻辑序号。"""
        self._seq += 1
        return self._seq

    def next_id(self, kind: str, prefix: str) -> str:
        """按实体类型分配可读的确定性标识，如 CLM-0001。"""
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return f"{prefix}-{self._counters[kind]:04d}"

    def emit(self, kind: str, payload: Mapping[str, object]) -> Event:
        event = Event(self.tick(), kind, canonical_json(dict(payload)))
        self.events.append(event)
        return event
