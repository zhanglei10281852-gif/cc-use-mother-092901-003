"""场景核证应用服务。

- ScenarioRegistry：场景注册与版本发布，版本树支持分叉。
- RunRecorder：运行记录的幂等收录，失败重跑不覆盖原始结果，重复上传不去重外增员。
- ClaimService：能力注册、能力声明起草与覆盖率统计。
- ReviewBoard：评审结论、发布批次冻结、局部场景退回、结论撤销及其传播。
- ImpactAnalyzer：场景变更对已签发声明的影响分析。
- TraceabilityService：能力 ↔ 场景 ↔ 运行 ↔ 证据的双向追溯。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from .contracts import RunOutcome
from .errors import ConflictError, NotFoundError, StateError, ValidationError
from .model import (
    BatchState,
    Capability,
    CapabilityClaim,
    ClaimStatus,
    ConclusionState,
    Evidence,
    ReleaseBatch,
    ReviewConclusion,
    RunRecord,
    ScenarioContent,
    ScenarioRef,
    ScenarioRevision,
    Verdict,
    content_digest,
)
from .store import Store

# ---------------------------------------------------------------------------
# 场景注册与版本管理
# ---------------------------------------------------------------------------


class ScenarioRegistry:
    """维护场景版本树：注册根版本、基于任一已存在版本发布后继（可分叉）。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    def register_scenario(
        self,
        scenario_id: str,
        content: ScenarioContent,
        author: str,
        change_note: str = "初始版本",
    ) -> ScenarioRevision:
        if not scenario_id:
            raise ValidationError("场景标识不能为空")
        if scenario_id in self._store.scenarios:
            raise ConflictError(f"场景 {scenario_id} 已存在")
        revision = ScenarioRevision(
            scenario_id=scenario_id,
            version=1,
            parent_version=None,
            content=content,
            content_hash=content.digest(),
            author=author,
            change_note=change_note,
            seq=self._store.tick(),
        )
        self._store.scenarios[scenario_id] = {1: revision}
        self._store.emit(
            "scenario_registered",
            {"scenario_id": scenario_id, "version": 1, "content_hash": revision.content_hash},
        )
        return revision

    def publish_revision(
        self,
        scenario_id: str,
        base_version: int,
        content: ScenarioContent,
        author: str,
        change_note: str,
    ) -> ScenarioRevision:
        """基于 base_version 发布新版本。对同一基版本多次发布即产生分叉。"""
        versions = self._store.scenarios.get(scenario_id)
        if versions is None:
            raise NotFoundError(f"场景 {scenario_id} 不存在")
        if base_version not in versions:
            raise NotFoundError(f"场景 {scenario_id} 的版本 {base_version} 不存在")
        revision = ScenarioRevision(
            scenario_id=scenario_id,
            version=max(versions) + 1,
            parent_version=base_version,
            content=content,
            content_hash=content.digest(),
            author=author,
            change_note=change_note,
            seq=self._store.tick(),
        )
        versions[revision.version] = revision
        self._store.emit(
            "revision_published",
            {
                "scenario_id": scenario_id,
                "version": revision.version,
                "parent_version": base_version,
                "content_hash": revision.content_hash,
            },
        )
        return revision

    def require(self, scenario_id: str, version: int) -> ScenarioRevision:
        versions = self._store.scenarios.get(scenario_id)
        if versions is None or version not in versions:
            raise NotFoundError(f"场景 {scenario_id} 的版本 {version} 不存在")
        return versions[version]

    def revisions(self, scenario_id: str) -> tuple[ScenarioRevision, ...]:
        if scenario_id not in self._store.scenarios:
            raise NotFoundError(f"场景 {scenario_id} 不存在")
        return tuple(self._store.scenarios[scenario_id][v] for v in sorted(self._store.scenarios[scenario_id]))

    def lineage(self, scenario_id: str, version: int) -> tuple[ScenarioRevision, ...]:
        """从根版本到指定版本的祖先链（含自身），顺序确定。"""
        chain: list[ScenarioRevision] = []
        cursor: int | None = version
        while cursor is not None:
            revision = self.require(scenario_id, cursor)
            chain.append(revision)
            cursor = revision.parent_version
        return tuple(reversed(chain))

    def heads(self, scenario_id: str) -> tuple[int, ...]:
        """版本树的叶子版本（未被任何版本当作父版本）。"""
        if scenario_id not in self._store.scenarios:
            raise NotFoundError(f"场景 {scenario_id} 不存在")
        versions = self._store.scenarios[scenario_id]
        parents = {r.parent_version for r in versions.values() if r.parent_version is not None}
        return tuple(sorted(v for v in versions if v not in parents))


# ---------------------------------------------------------------------------
# 运行记录
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunIngestion:
    """收录结果：run 为（既有或新建的）运行记录，created 表示是否新建。"""

    run: RunRecord
    created: bool


class RunRecorder:
    """收录测试运行。

    - 幂等：run_id 由（场景版本、软件版本、输入摘要、结果、证据）内容寻址生成，
      重复上传同一运行返回既有记录，不产生新记录，覆盖率不变。
    - 仅追加：输入相同但结果或证据不同的重跑生成新记录并递增 attempt，
      原始记录保持可查询，失败重跑不会覆盖原始结果。
    """

    def __init__(self, store: Store, registry: ScenarioRegistry) -> None:
        self._store = store
        self._registry = registry

    def record_run(
        self,
        scenario_id: str,
        scenario_version: int,
        software_version: str,
        input_digest: str,
        outcome: RunOutcome | str,
        evidence: tuple[Evidence, ...] = (),
        external_id: str | None = None,
    ) -> RunIngestion:
        self._registry.require(scenario_id, scenario_version)
        if not software_version:
            raise ValidationError("必须记录被测软件版本")
        if not input_digest:
            raise ValidationError("必须记录输入摘要")
        outcome = RunOutcome(outcome)
        norm_evidence = tuple(sorted(evidence))
        key = {
            "scenario_id": scenario_id,
            "scenario_version": scenario_version,
            "software_version": software_version,
            "input_digest": input_digest,
            "outcome": outcome.value,
            "evidence": [asdict(e) for e in norm_evidence],
        }
        run_id = "RUN-" + content_digest(key)[:16]
        existing = self._store.runs.get(run_id)
        if existing is not None:
            self._store.emit("run_deduplicated", {"run_id": run_id})
            return RunIngestion(existing, created=False)
        attempt = 1 + sum(
            1
            for r in self._store.runs.values()
            if (r.scenario_id, r.scenario_version, r.software_version, r.input_digest)
            == (scenario_id, scenario_version, software_version, input_digest)
        )
        run = RunRecord(
            run_id=run_id,
            scenario_id=scenario_id,
            scenario_version=scenario_version,
            software_version=software_version,
            input_digest=input_digest,
            outcome=outcome,
            evidence=norm_evidence,
            attempt=attempt,
            external_id=external_id,
            seq=self._store.tick(),
        )
        self._store.runs[run_id] = run
        self._store.emit(
            "run_recorded",
            {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "scenario_version": scenario_version,
                "outcome": outcome.value,
                "attempt": attempt,
            },
        )
        return RunIngestion(run, created=True)

    def get(self, run_id: str) -> RunRecord:
        run = self._store.runs.get(run_id)
        if run is None:
            raise NotFoundError(f"运行 {run_id} 不存在")
        return run


# ---------------------------------------------------------------------------
# 能力与能力声明
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageReport:
    """能力覆盖报告：按场景版本去重后的通过运行覆盖情况。"""

    capability_id: str
    required_scenarios: tuple[str, ...]
    covered_refs: tuple[ScenarioRef, ...]
    missing_scenarios: tuple[str, ...]

    @property
    def covered_count(self) -> int:
        return len(self.covered_refs)

    @property
    def complete(self) -> bool:
        return not self.missing_scenarios


class ClaimService:
    """能力注册、能力声明起草与覆盖率统计。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    def register_capability(
        self,
        capability_id: str,
        name: str,
        required_scenarios: tuple[str, ...],
    ) -> Capability:
        if not capability_id or not name:
            raise ValidationError("能力标识与名称不能为空")
        required = tuple(sorted(set(required_scenarios)))
        if not required:
            raise ValidationError("能力必须声明至少一个待覆盖场景")
        for scenario_id in required:
            if scenario_id not in self._store.scenarios:
                raise NotFoundError(f"场景 {scenario_id} 不存在")
        if capability_id in self._store.capabilities:
            raise ConflictError(f"能力 {capability_id} 已注册")
        capability = Capability(capability_id, name, required, seq=self._store.tick())
        self._store.capabilities[capability_id] = capability
        self._store.emit(
            "capability_registered",
            {"capability_id": capability_id, "required_scenarios": list(required)},
        )
        return capability

    def require_capability(self, capability_id: str) -> Capability:
        capability = self._store.capabilities.get(capability_id)
        if capability is None:
            raise NotFoundError(f"能力 {capability_id} 不存在")
        return capability

    def require_claim(self, claim_id: str) -> CapabilityClaim:
        claim = self._store.claims.get(claim_id)
        if claim is None:
            raise NotFoundError(f"能力声明 {claim_id} 不存在")
        return claim

    def _validated_runs(self, capability: Capability, run_ids: tuple[str, ...]) -> tuple[RunRecord, ...]:
        runs: list[RunRecord] = []
        for run_id in run_ids:
            run = self._store.runs.get(run_id)
            if run is None:
                raise NotFoundError(f"运行 {run_id} 不存在")
            if run.outcome is not RunOutcome.PASSED:
                raise ValidationError(f"运行 {run_id} 未通过，不能支撑能力声明")
            if run.scenario_id not in capability.required_scenarios:
                raise ValidationError(
                    f"运行 {run_id} 所属场景 {run.scenario_id} 不在能力 {capability.capability_id} 的待覆盖集合内"
                )
            runs.append(run)
        return tuple(runs)

    def draft_claim(self, capability_id: str, run_ids: tuple[str, ...]) -> CapabilityClaim:
        """起草能力声明：引用的运行必须全部通过，且覆盖能力的全部待覆盖场景。"""
        capability = self.require_capability(capability_id)
        unique_run_ids = tuple(sorted(set(run_ids)))
        if not unique_run_ids:
            raise ValidationError("能力声明至少引用一次运行")
        runs = self._validated_runs(capability, unique_run_ids)
        covered = {r.scenario_id for r in runs}
        missing = [s for s in capability.required_scenarios if s not in covered]
        if missing:
            raise ValidationError(f"能力声明未覆盖场景: {', '.join(missing)}")
        claim_id = self._store.next_id("claim", "CLM")
        claim = CapabilityClaim(
            claim_id=claim_id,
            capability_id=capability_id,
            scenario_refs=tuple(sorted({r.ref for r in runs})),
            run_ids=unique_run_ids,
            status=ClaimStatus.DRAFT,
            returned_refs=(),
            conclusion_id=None,
            batch_id=None,
            suspended_seq=None,
            seq=self._store.tick(),
        )
        self._store.claims[claim_id] = claim
        self._store.emit(
            "claim_drafted",
            {"claim_id": claim_id, "capability_id": capability_id, "run_ids": list(unique_run_ids)},
        )
        return claim

    def attach_runs(self, claim_id: str, run_ids: tuple[str, ...]) -> CapabilityClaim:
        """向草稿或待补证声明追加通过运行（退回补证的入口）。"""
        claim = self.require_claim(claim_id)
        if claim.status not in (ClaimStatus.DRAFT, ClaimStatus.SUSPENDED):
            raise StateError(f"声明 {claim_id} 当前状态不允许追加运行")
        capability = self.require_capability(claim.capability_id)
        new_runs = self._validated_runs(capability, tuple(sorted(set(run_ids))))
        merged_ids = tuple(sorted(set(claim.run_ids) | {r.run_id for r in new_runs}))
        all_runs = tuple(self._store.runs[r] for r in merged_ids)
        claim = replace(
            claim,
            run_ids=merged_ids,
            scenario_refs=tuple(sorted({r.ref for r in all_runs})),
        )
        self._store.claims[claim_id] = claim
        self._store.emit("runs_attached", {"claim_id": claim_id, "run_ids": list(merged_ids)})
        return claim

    def coverage_report(self, capability_id: str) -> CoverageReport:
        """覆盖率按场景版本去重：同一运行重复上传、同一版本的多次运行都只计一次。"""
        capability = self.require_capability(capability_id)
        passing = [
            r
            for r in self._store.runs.values()
            if r.outcome is RunOutcome.PASSED and r.scenario_id in capability.required_scenarios
        ]
        covered_refs = tuple(sorted({r.ref for r in passing}))
        covered_ids = {r.scenario_id for r in passing}
        missing = tuple(s for s in capability.required_scenarios if s not in covered_ids)
        return CoverageReport(capability_id, capability.required_scenarios, covered_refs, missing)


# ---------------------------------------------------------------------------
# 评审：结论、发布批次、退回与撤销
# ---------------------------------------------------------------------------


class ReviewBoard:
    """评审操作。所有状态迁移通过事件日志记录，传播顺序确定。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    def _require_claim(self, claim_id: str) -> CapabilityClaim:
        claim = self._store.claims.get(claim_id)
        if claim is None:
            raise NotFoundError(f"能力声明 {claim_id} 不存在")
        return claim

    def _require_batch(self, batch_id: str) -> ReleaseBatch:
        batch = self._store.batches.get(batch_id)
        if batch is None:
            raise NotFoundError(f"发布批次 {batch_id} 不存在")
        return batch

    def submit_conclusion(
        self,
        claim_id: str,
        verdict: Verdict | str,
        reviewer: str,
        rationale: str,
    ) -> ReviewConclusion:
        """对草稿或待补证声明给出评审结论。

        APPROVE：声明转为已签发；对待补证声明，要求每个被退回场景都已有
        退回之后记录的通过运行补证。RETURN：声明保持草稿，等待修改后重新评审。
        """
        claim = self._require_claim(claim_id)
        verdict = Verdict(verdict)
        if claim.status not in (ClaimStatus.DRAFT, ClaimStatus.SUSPENDED):
            raise StateError(f"声明 {claim_id} 当前状态不允许评审")
        if verdict is Verdict.APPROVE and claim.status is ClaimStatus.SUSPENDED:
            unresolved = [ref.label() for ref in claim.returned_refs if not self._resolved(claim, ref)]
            if unresolved:
                raise ValidationError(f"退回场景尚未补证: {', '.join(unresolved)}")
        conclusion = ReviewConclusion(
            conclusion_id=self._store.next_id("conclusion", "CON"),
            claim_id=claim_id,
            verdict=verdict,
            reviewer=reviewer,
            rationale=rationale,
            state=ConclusionState.ACTIVE,
            seq=self._store.tick(),
        )
        self._store.conclusions[conclusion.conclusion_id] = conclusion
        self._store.emit(
            "conclusion_recorded",
            {
                "conclusion_id": conclusion.conclusion_id,
                "claim_id": claim_id,
                "verdict": verdict.value,
                "reviewer": reviewer,
            },
        )
        if verdict is Verdict.APPROVE:
            claim = replace(
                claim,
                status=ClaimStatus.ISSUED,
                returned_refs=(),
                conclusion_id=conclusion.conclusion_id,
                suspended_seq=None,
            )
            self._store.claims[claim_id] = claim
            self._store.emit(
                "claim_issued",
                {"claim_id": claim_id, "conclusion_id": conclusion.conclusion_id},
            )
        return conclusion

    def _resolved(self, claim: CapabilityClaim, ref: ScenarioRef) -> bool:
        """退回场景是否已补证：退回之后（按逻辑序号）在同一场景上存在已附带的通过运行。"""
        if claim.suspended_seq is None:
            return False
        return any(
            self._store.runs[run_id].scenario_id == ref.scenario_id
            and self._store.runs[run_id].outcome is RunOutcome.PASSED
            and self._store.runs[run_id].seq > claim.suspended_seq
            for run_id in claim.run_ids
        )

    def create_batch(self, claim_ids: tuple[str, ...]) -> ReleaseBatch:
        claims = self._issuable_claims(claim_ids)
        batch = ReleaseBatch(
            batch_id=self._store.next_id("batch", "BAT"),
            claim_ids=tuple(c.claim_id for c in claims),
            state=BatchState.OPEN,
            seq=self._store.tick(),
        )
        self._store.batches[batch.batch_id] = batch
        for claim in claims:
            self._store.claims[claim.claim_id] = replace(claim, batch_id=batch.batch_id)
        self._store.emit(
            "batch_created",
            {"batch_id": batch.batch_id, "claim_ids": list(batch.claim_ids)},
        )
        return batch

    def add_claims(self, batch_id: str, claim_ids: tuple[str, ...]) -> ReleaseBatch:
        batch = self._require_batch(batch_id)
        if batch.state is not BatchState.OPEN:
            raise StateError(f"发布批次 {batch_id} 已冻结，不能变更成员")
        claims = self._issuable_claims(claim_ids)
        merged = tuple(sorted(set(batch.claim_ids) | {c.claim_id for c in claims}))
        batch = replace(batch, claim_ids=merged)
        self._store.batches[batch_id] = batch
        for claim in claims:
            self._store.claims[claim.claim_id] = replace(claim, batch_id=batch_id)
        self._store.emit(
            "claims_added_to_batch",
            {"batch_id": batch_id, "claim_ids": [c.claim_id for c in claims]},
        )
        return batch

    def _issuable_claims(self, claim_ids: tuple[str, ...]) -> tuple[CapabilityClaim, ...]:
        unique_ids = tuple(sorted(set(claim_ids)))
        if not unique_ids:
            raise ValidationError("发布批次至少包含一份声明")
        claims: list[CapabilityClaim] = []
        for claim_id in unique_ids:
            claim = self._require_claim(claim_id)
            if claim.status is not ClaimStatus.ISSUED:
                raise StateError(f"声明 {claim_id} 未签发，不能进入发布批次")
            if claim.batch_id is not None:
                raise ConflictError(f"声明 {claim_id} 已在发布批次 {claim.batch_id} 中")
            claims.append(claim)
        return tuple(claims)

    def freeze_batch(self, batch_id: str) -> ReleaseBatch:
        """冻结发布批次：成员固定，之后的退回与撤销仍可作为质量兜底执行。"""
        batch = self._require_batch(batch_id)
        if batch.state is not BatchState.OPEN:
            raise StateError(f"发布批次 {batch_id} 已冻结")
        batch = replace(batch, state=BatchState.FROZEN)
        self._store.batches[batch_id] = batch
        self._store.emit("batch_frozen", {"batch_id": batch_id})
        return batch

    def return_scenarios(
        self,
        batch_id: str,
        claim_id: str,
        refs: tuple[ScenarioRef, ...],
        reviewer: str,
        rationale: str,
    ) -> CapabilityClaim:
        """退回声明中的局部场景：声明转为待补证，需补证后重新评审签发。"""
        batch = self._require_batch(batch_id)
        if claim_id not in batch.claim_ids:
            raise NotFoundError(f"声明 {claim_id} 不在发布批次 {batch_id} 中")
        claim = self._require_claim(claim_id)
        if claim.status is not ClaimStatus.ISSUED:
            raise StateError(f"声明 {claim_id} 未处于已签发状态，不能退回局部场景")
        norm_refs = tuple(sorted(set(refs)))
        if not norm_refs:
            raise ValidationError("退回至少一个场景版本")
        outside = [ref.label() for ref in norm_refs if ref not in claim.scenario_refs]
        if outside:
            raise ValidationError(f"退回的场景不在声明范围内: {', '.join(outside)}")
        event = self._store.emit(
            "scenarios_returned",
            {
                "batch_id": batch_id,
                "claim_id": claim_id,
                "refs": [ref.label() for ref in norm_refs],
                "reviewer": reviewer,
                "rationale": rationale,
            },
        )
        claim = replace(
            claim,
            status=ClaimStatus.SUSPENDED,
            returned_refs=norm_refs,
            suspended_seq=event.seq,
        )
        self._store.claims[claim_id] = claim
        return claim

    def revoke_conclusion(self, conclusion_id: str, reviewer: str, reason: str) -> ReviewConclusion:
        """撤销评审结论，并确定性地传播：凡以该结论为签发依据的声明一并撤销。

        若声明已依据更新的结论重新签发，则撤销旧结论不影响声明。
        """
        conclusion = self._store.conclusions.get(conclusion_id)
        if conclusion is None:
            raise NotFoundError(f"评审结论 {conclusion_id} 不存在")
        if conclusion.state is not ConclusionState.ACTIVE:
            raise StateError(f"评审结论 {conclusion_id} 已被撤销")
        conclusion = replace(conclusion, state=ConclusionState.REVOKED)
        self._store.conclusions[conclusion_id] = conclusion
        self._store.emit(
            "conclusion_revoked",
            {"conclusion_id": conclusion_id, "reviewer": reviewer, "reason": reason},
        )
        claim = self._store.claims[conclusion.claim_id]
        if claim.conclusion_id == conclusion_id and claim.status in (
            ClaimStatus.ISSUED,
            ClaimStatus.SUSPENDED,
        ):
            claim = replace(claim, status=ClaimStatus.REVOKED)
            self._store.claims[claim.claim_id] = claim
            self._store.emit(
                "claim_revoked",
                {"claim_id": claim.claim_id, "conclusion_id": conclusion_id},
            )
        return conclusion


# ---------------------------------------------------------------------------
# 变更影响分析
# ---------------------------------------------------------------------------


class ImpactAnalyzer:
    """场景变更影响分析：新版本发布后，计算哪些已签发声明的证据落在被超越的旧版本上。"""

    def __init__(self, store: Store, registry: ScenarioRegistry) -> None:
        self._store = store
        self._registry = registry

    def impacted_claims(self, scenario_id: str, version: int) -> tuple[str, ...]:
        """返回引用了新版本任一祖先版本的已签发（含待补证）声明，按标识排序。"""
        self._registry.require(scenario_id, version)
        ancestors = {r.version for r in self._registry.lineage(scenario_id, version)} - {version}
        if not ancestors:
            return ()
        impacted = [
            claim.claim_id
            for claim in self._store.claims.values()
            if claim.status in (ClaimStatus.ISSUED, ClaimStatus.SUSPENDED)
            and any(
                ref.scenario_id == scenario_id and ref.version in ancestors
                for ref in claim.scenario_refs
            )
        ]
        return tuple(sorted(impacted))


# ---------------------------------------------------------------------------
# 双向追溯
# ---------------------------------------------------------------------------


class TraceabilityService:
    """能力 → 声明 → 场景版本 → 运行 → 证据的正向追溯，以及反向检索。"""

    def __init__(self, store: Store) -> None:
        self._store = store

    @staticmethod
    def _ref_view(ref: ScenarioRef) -> dict:
        return {"scenario_id": ref.scenario_id, "version": ref.version}

    def _revision_view(self, ref: ScenarioRef) -> dict:
        revision = self._store.scenarios[ref.scenario_id][ref.version]
        return {
            "scenario_id": revision.scenario_id,
            "version": revision.version,
            "parent_version": revision.parent_version,
            "content_hash": revision.content_hash,
            "author": revision.author,
            "change_note": revision.change_note,
        }

    def _run_view(self, run: RunRecord) -> dict:
        return {
            "run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "scenario_version": run.scenario_version,
            "software_version": run.software_version,
            "input_digest": run.input_digest,
            "outcome": run.outcome.value,
            "attempt": run.attempt,
            "external_id": run.external_id,
            "evidence": [asdict(e) for e in run.evidence],
        }

    def _conclusion_view(self, conclusion: ReviewConclusion) -> dict:
        return {
            "conclusion_id": conclusion.conclusion_id,
            "claim_id": conclusion.claim_id,
            "verdict": conclusion.verdict.value,
            "reviewer": conclusion.reviewer,
            "rationale": conclusion.rationale,
            "state": conclusion.state.value,
        }

    def _claim_view(self, claim: CapabilityClaim, *, deep: bool = False) -> dict:
        view = {
            "claim_id": claim.claim_id,
            "capability_id": claim.capability_id,
            "status": claim.status.value,
            "batch_id": claim.batch_id,
            "conclusion_id": claim.conclusion_id,
            "scenario_refs": [self._ref_view(ref) for ref in claim.scenario_refs],
            "returned_refs": [self._ref_view(ref) for ref in claim.returned_refs],
            "run_ids": list(claim.run_ids),
        }
        if deep:
            view["scenarios"] = [self._revision_view(ref) for ref in claim.scenario_refs]
            view["runs"] = [self._run_view(self._store.runs[run_id]) for run_id in claim.run_ids]
            conclusion = (
                self._store.conclusions.get(claim.conclusion_id) if claim.conclusion_id else None
            )
            view["conclusion"] = self._conclusion_view(conclusion) if conclusion else None
        return view

    def _claims_for_run(self, run_id: str) -> list[CapabilityClaim]:
        return sorted(
            (c for c in self._store.claims.values() if run_id in c.run_ids),
            key=lambda c: c.claim_id,
        )

    def capability_trace(self, capability_id: str) -> dict:
        """正向追溯：能力 → 声明 → 场景版本 → 运行 → 证据。"""
        capability = self._store.capabilities.get(capability_id)
        if capability is None:
            raise NotFoundError(f"能力 {capability_id} 不存在")
        claims = sorted(
            (c for c in self._store.claims.values() if c.capability_id == capability_id),
            key=lambda c: c.claim_id,
        )
        return {
            "capability": {
                "capability_id": capability.capability_id,
                "name": capability.name,
                "required_scenarios": list(capability.required_scenarios),
            },
            "claims": [self._claim_view(c, deep=True) for c in claims],
        }

    def claim_trace(self, claim_id: str) -> dict:
        claim = self._store.claims.get(claim_id)
        if claim is None:
            raise NotFoundError(f"能力声明 {claim_id} 不存在")
        return self._claim_view(claim, deep=True)

    def run_trace(self, run_id: str) -> dict:
        """反向追溯：运行 → 场景版本 → 引用它的声明 → 能力与评审结论。"""
        run = self._store.runs.get(run_id)
        if run is None:
            raise NotFoundError(f"运行 {run_id} 不存在")
        claims = self._claims_for_run(run_id)
        return {
            "run": self._run_view(run),
            "scenario": self._revision_view(run.ref),
            "claims": [self._claim_view(c) for c in claims],
            "capabilities": sorted({c.capability_id for c in claims}),
            "conclusions": [
                self._conclusion_view(self._store.conclusions[c.conclusion_id])
                for c in claims
                if c.conclusion_id
            ],
        }

    def scenario_trace(self, scenario_id: str, version: int) -> dict:
        """反向追溯：场景版本 → 运行 → 声明 → 能力。"""
        versions = self._store.scenarios.get(scenario_id)
        if versions is None or version not in versions:
            raise NotFoundError(f"场景 {scenario_id} 的版本 {version} 不存在")
        ref = ScenarioRef(scenario_id, version)
        runs = sorted(
            (
                r
                for r in self._store.runs.values()
                if r.scenario_id == scenario_id and r.scenario_version == version
            ),
            key=lambda r: r.run_id,
        )
        claims = sorted(
            (c for c in self._store.claims.values() if ref in c.scenario_refs),
            key=lambda c: c.claim_id,
        )
        return {
            "scenario": self._revision_view(ref),
            "runs": [self._run_view(r) for r in runs],
            "claims": [self._claim_view(c) for c in claims],
            "capabilities": sorted({c.capability_id for c in claims}),
        }

    def evidence_trace(self, digest: str) -> dict:
        """反向追溯：证据摘要 → 运行 → 声明 → 能力。"""
        runs = sorted(
            (r for r in self._store.runs.values() if any(e.digest == digest for e in r.evidence)),
            key=lambda r: r.run_id,
        )
        claims: dict[str, CapabilityClaim] = {}
        for run in runs:
            for claim in self._claims_for_run(run.run_id):
                claims[claim.claim_id] = claim
        ordered_claims = [claims[k] for k in sorted(claims)]
        return {
            "evidence_digest": digest,
            "runs": [self._run_view(r) for r in runs],
            "claims": [self._claim_view(c) for c in ordered_claims],
            "capabilities": sorted({c.capability_id for c in ordered_claims}),
        }
