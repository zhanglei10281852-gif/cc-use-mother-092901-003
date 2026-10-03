"""场景核证后端门面：组合各服务，并提供全量状态快照用于确定性校验。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .model import ScenarioContent, ScenarioRevision, canonical_json
from .services import (
    ClaimService,
    ImpactAnalyzer,
    ReviewBoard,
    RunRecorder,
    ScenarioRegistry,
    TraceabilityService,
)
from .store import Store


@dataclass(frozen=True)
class RevisionImpact:
    """版本发布结果：新版本与受影响的已签发能力声明。"""

    revision: ScenarioRevision
    impacted_claim_ids: tuple[str, ...]


class CertificationBackend:
    """场景核证后端入口。

    用法概览：
    - scenarios：注册场景、发布版本（支持分叉）
    - runs：收录测试运行（幂等去重、重跑仅追加）
    - claims：注册能力、起草声明、覆盖率统计
    - review：评审结论、发布批次冻结、局部退回、结论撤销
    - impact：场景变更影响分析
    - trace：能力 ↔ 场景 ↔ 运行 ↔ 证据双向追溯
    """

    def __init__(self) -> None:
        self.store = Store()
        self.scenarios = ScenarioRegistry(self.store)
        self.runs = RunRecorder(self.store, self.scenarios)
        self.claims = ClaimService(self.store)
        self.review = ReviewBoard(self.store)
        self.impact = ImpactAnalyzer(self.store, self.scenarios)
        self.trace = TraceabilityService(self.store)

    def publish_revision(
        self,
        scenario_id: str,
        base_version: int,
        content: ScenarioContent,
        author: str,
        change_note: str,
    ) -> RevisionImpact:
        """发布场景新版本，并同时给出受影响的已签发能力声明。"""
        revision = self.scenarios.publish_revision(
            scenario_id, base_version, content, author, change_note
        )
        impacted = self.impact.impacted_claims(scenario_id, revision.version)
        return RevisionImpact(revision=revision, impacted_claim_ids=impacted)

    def snapshot(self) -> str:
        """导出全量状态的规范 JSON：同一操作序列必然得到同一文本。"""
        store = self.store
        state = {
            "scenarios": [
                asdict(revision)
                for scenario_id in sorted(store.scenarios)
                for _, revision in sorted(store.scenarios[scenario_id].items())
            ],
            "capabilities": [asdict(c) for _, c in sorted(store.capabilities.items())],
            "runs": [asdict(r) for _, r in sorted(store.runs.items())],
            "claims": [asdict(c) for _, c in sorted(store.claims.items())],
            "conclusions": [asdict(c) for _, c in sorted(store.conclusions.items())],
            "batches": [asdict(b) for _, b in sorted(store.batches.items())],
            "events": [asdict(e) for e in store.events],
        }
        return canonical_json(state)
