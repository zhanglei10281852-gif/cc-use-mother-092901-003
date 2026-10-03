"""场景核证后端：版本化场景目录、运行证据、覆盖计算、评审签发与追溯。

设计不变量：

1. 场景版本不可变——变更通过分叉产生新版本，旧版本及其运行记录原样保留；
2. 运行记录只增不改——重复上传同一运行幂等去重，失败重跑以新记录链接，
   绝不覆盖原始结果；
3. 覆盖与影响分析结果确定——所有返回集合按标识排序，同一操作序列
   在任何时刻重放都得到相同状态；
4. 评审结论可撤销且撤销会传播——支撑证据失效的已签发声明被确定地
   标记为 AFFECTED，必须重新签发才能恢复。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping

from .contracts import RunOutcome
from .errors import ConflictError, CoverageError, NotFoundError, StateError
from .models import (
    AmbientConditions,
    BatchStatus,
    CapabilityClaim,
    ClaimStatus,
    CoverageReport,
    Event,
    Evidence,
    ExpectedBehavior,
    FaultInjection,
    ForkResult,
    ImpactReport,
    PinCoverage,
    ReleaseBatch,
    ReviewVerdict,
    RoadEnvironment,
    ScenarioDefinition,
    ScenarioPin,
    TestRunRecord,
    TrafficParticipant,
    VerdictDecision,
    normalize_parameters,
)

__all__ = ["ScenarioCertificationBackend"]

# 重复上传判定时比较的语义字段（recorded_at/recorded_by 允许因重试而不同）
_RUN_SEMANTIC_FIELDS = (
    "scenario_id", "scenario_version", "software_version", "input_digest",
    "outcome", "evidence", "executed_at", "rerun_of",
)


class ScenarioCertificationBackend:
    """内存版场景核证后端，提供命令与追溯查询接口。"""

    def __init__(self) -> None:
        self._scenarios: dict[tuple[str, int], ScenarioDefinition] = {}
        self._runs: dict[str, TestRunRecord] = {}
        self._verdicts: dict[str, ReviewVerdict] = {}
        self._verdict_by_run: dict[str, str] = {}
        self._revoked_verdicts: set[str] = set()
        self._claims: dict[str, CapabilityClaim] = {}
        self._batches: dict[str, ReleaseBatch] = {}
        self._events: list[Event] = []

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _emit(self, kind: str, subject: str, at: str, **detail: object) -> None:
        self._events.append(Event(
            seq=len(self._events) + 1,
            kind=kind,
            subject=subject,
            detail=tuple(sorted((k, str(v)) for k, v in detail.items())),
            at=at,
        ))

    def _require_scenario(self, scenario_id: str, version: int) -> ScenarioDefinition:
        try:
            return self._scenarios[(scenario_id, version)]
        except KeyError:
            raise NotFoundError(f"场景版本不存在: {scenario_id}@v{version}") from None

    def _require_run(self, run_id: str) -> TestRunRecord:
        try:
            return self._runs[run_id]
        except KeyError:
            raise NotFoundError(f"运行不存在: {run_id}") from None

    def _require_claim(self, claim_id: str) -> CapabilityClaim:
        try:
            return self._claims[claim_id]
        except KeyError:
            raise NotFoundError(f"能力声明不存在: {claim_id}") from None

    def _require_batch(self, batch_id: str) -> ReleaseBatch:
        try:
            return self._batches[batch_id]
        except KeyError:
            raise NotFoundError(f"发布批次不存在: {batch_id}") from None

    def _transition_claim(
        self,
        claim: CapabilityClaim,
        status: ClaimStatus,
        *,
        reason: str,
        at: str,
        event: str,
        **extra: object,
    ) -> CapabilityClaim:
        updated = replace(claim, status=status, status_reason=reason, **extra)
        self._claims[claim.claim_id] = updated
        self._emit(event, claim.claim_id, at, status=status.value, reason=reason)
        return updated

    # ------------------------------------------------------------------
    # 场景目录：创建与分叉
    # ------------------------------------------------------------------

    def _register(self, definition: ScenarioDefinition) -> ScenarioDefinition:
        key = (definition.scenario_id, definition.version)
        if key in self._scenarios:
            raise ConflictError(f"场景版本已存在: {definition.scenario_id}@v{definition.version}")
        latest = self.latest_version(definition.scenario_id)
        if latest is None:
            if definition.version != 1 or definition.parent_version is not None:
                raise StateError("新场景必须从版本 1 开始且无父版本")
        else:
            if definition.version != latest + 1:
                raise StateError(
                    f"场景版本必须连续递增: 当前最新 v{latest}，拒绝注册 v{definition.version}")
            if definition.parent_version is not None:
                self._require_scenario(definition.scenario_id, definition.parent_version)
        self._scenarios[key] = definition
        self._emit("scenario_registered", definition.scenario_id, definition.created_at,
                   version=definition.version,
                   digest=definition.content_digest())
        return definition

    def create_scenario(
        self,
        scenario_id: str,
        title: str,
        *,
        road: RoadEnvironment,
        participants: Iterable[TrafficParticipant],
        ambient: AmbientConditions,
        faults: Iterable[FaultInjection] = (),
        expected: Iterable[ExpectedBehavior],
        parameters: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        reason: str,
        at: str,
        by: str,
    ) -> ScenarioDefinition:
        """登记一个全新场景的版本 1。"""
        if self.latest_version(scenario_id) is not None:
            raise ConflictError(f"场景 {scenario_id} 已存在，请使用 fork_scenario 变更")
        return self._register(ScenarioDefinition(
            scenario_id=scenario_id, version=1, title=title, road=road,
            participants=tuple(participants), ambient=ambient, faults=tuple(faults),
            expected=tuple(expected), parameters=normalize_parameters(parameters),
            parent_version=None, change_reason=reason, created_at=at, created_by=by,
        ))

    def fork_scenario(
        self,
        scenario_id: str,
        *,
        reason: str,
        at: str,
        by: str,
        base_version: int | None = None,
        **changes: object,
    ) -> ForkResult:
        """从既有版本分叉出新版本，并计算受影响的已签发声明。

        changes 可包含 title/road/participants/ambient/faults/expected/parameters
        中的任意字段；未给出的字段继承基线版本。基线默认为最新版本，
        也可指定历史版本实现真正的版本分叉。
        """
        base = self.get_scenario(scenario_id, base_version)
        latest = self.latest_version(scenario_id)
        assert latest is not None  # get_scenario 已成功即存在
        allowed = {"title", "road", "participants", "ambient",
                   "faults", "expected", "parameters"}
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"未知场景字段: {unknown}")
        if "parameters" in changes:
            changes["parameters"] = normalize_parameters(changes["parameters"])
        for key in ("participants", "faults", "expected"):
            if key in changes:
                changes[key] = tuple(changes[key])
        definition = replace(
            base, version=latest + 1, parent_version=base.version,
            change_reason=reason, created_at=at, created_by=by, **changes)
        self._register(definition)
        self._emit("scenario_forked", scenario_id, at,
                   version=definition.version, parent=base.version, reason=reason)
        impact = self._apply_scenario_impact(scenario_id, definition.version, at)
        return ForkResult(definition=definition, impact=impact)

    def _apply_scenario_impact(self, scenario_id: str, new_version: int, at: str) -> ImpactReport:
        """场景发布新版本后，钉住该场景的已签发声明一律标记为受影响。"""
        affected: list[str] = []
        for claim_id in sorted(self._claims):
            claim = self._claims[claim_id]
            if claim.status is not ClaimStatus.ISSUED:
                continue
            if any(pin.scenario_id == scenario_id for pin in claim.pins):
                self._transition_claim(
                    claim, ClaimStatus.AFFECTED,
                    reason=f"场景 {scenario_id} 已发布新版本 v{new_version}",
                    at=at, event="claim_affected")
                affected.append(claim_id)
        return ImpactReport(scenario_id=scenario_id, new_version=new_version,
                            affected_claims=tuple(affected))

    # ------------------------------------------------------------------
    # 场景目录：查询
    # ------------------------------------------------------------------

    def latest_version(self, scenario_id: str) -> int | None:
        versions = [v for sid, v in self._scenarios if sid == scenario_id]
        return max(versions) if versions else None

    def list_versions(self, scenario_id: str) -> tuple[int, ...]:
        versions = sorted(v for sid, v in self._scenarios if sid == scenario_id)
        if not versions:
            raise NotFoundError(f"场景不存在: {scenario_id}")
        return tuple(versions)

    def get_scenario(self, scenario_id: str, version: int | None = None) -> ScenarioDefinition:
        if version is None:
            version = self.latest_version(scenario_id)
            if version is None:
                raise NotFoundError(f"场景不存在: {scenario_id}")
        return self._require_scenario(scenario_id, version)

    # ------------------------------------------------------------------
    # 测试运行：幂等录入
    # ------------------------------------------------------------------

    def record_run(self, run: TestRunRecord) -> tuple[TestRunRecord, bool]:
        """录入一次运行；返回 (记录, 是否新建)。

        - run_id 已存在且语义内容相同：幂等去重，返回既有记录，
          覆盖率不会因重复上传而增加；
        - run_id 已存在但内容不同：拒绝，运行记录不可覆盖，
          失败重跑必须使用新的 run_id 并以 rerun_of 链接。
        """
        self._require_scenario(run.scenario_id, run.scenario_version)
        existing = self._runs.get(run.run_id)
        if existing is not None:
            same = all(getattr(existing, f) == getattr(run, f) for f in _RUN_SEMANTIC_FIELDS)
            if same:
                self._emit("run_duplicate_ignored", run.run_id, run.recorded_at)
                return existing, False
            raise ConflictError(f"运行 {run.run_id} 已存在且内容不同，运行记录不可覆盖")
        if run.rerun_of is not None:
            self._require_run(run.rerun_of)
        self._runs[run.run_id] = run
        self._emit("run_recorded", run.run_id, run.recorded_at,
                   scenario=f"{run.scenario_id}@v{run.scenario_version}",
                   outcome=run.outcome.value)
        return run, True

    def get_run(self, run_id: str) -> TestRunRecord:
        return self._require_run(run_id)

    # ------------------------------------------------------------------
    # 评审结论：提交与撤销
    # ------------------------------------------------------------------

    def submit_verdict(self, verdict: ReviewVerdict) -> ReviewVerdict:
        self._require_run(verdict.run_id)
        if verdict.verdict_id in self._verdicts:
            raise ConflictError(f"评审结论已存在: {verdict.verdict_id}")
        if self.active_verdict(verdict.run_id) is not None:
            raise StateError(f"运行 {verdict.run_id} 已有有效评审结论，需先撤销再提交")
        self._verdicts[verdict.verdict_id] = verdict
        self._verdict_by_run[verdict.run_id] = verdict.verdict_id
        self._emit("verdict_submitted", verdict.verdict_id, verdict.decided_at,
                   run=verdict.run_id, decision=verdict.decision.value)
        return verdict

    def active_verdict(self, run_id: str) -> ReviewVerdict | None:
        verdict_id = self._verdict_by_run.get(run_id)
        return self._verdicts[verdict_id] if verdict_id else None

    def revoke_verdict(self, verdict_id: str, *, reason: str, at: str, by: str) -> tuple[str, ...]:
        """撤销一条评审结论，并把失去支撑证据的已签发声明标记为受影响。

        返回受影响的声明标识（按序）。撤销是安全动作，冻结批次内的声明
        同样会被传播标记。
        """
        verdict = self._verdicts.get(verdict_id)
        if verdict is None:
            raise NotFoundError(f"评审结论不存在: {verdict_id}")
        if verdict_id in self._revoked_verdicts:
            raise StateError(f"评审结论 {verdict_id} 已撤销")
        self._revoked_verdicts.add(verdict_id)
        self._verdict_by_run.pop(verdict.run_id, None)
        self._emit("verdict_revoked", verdict_id, at, run=verdict.run_id,
                   reason=reason, by=by)
        return self._propagate_coverage_loss(at)

    def _propagate_coverage_loss(self, at: str) -> tuple[str, ...]:
        affected: list[str] = []
        for claim_id in sorted(self._claims):
            claim = self._claims[claim_id]
            if claim.status is not ClaimStatus.ISSUED:
                continue
            if self._uncovered_pins(claim):
                self._transition_claim(
                    claim, ClaimStatus.AFFECTED,
                    reason="支撑证据的评审结论被撤销",
                    at=at, event="claim_affected")
                affected.append(claim_id)
        return tuple(affected)

    # ------------------------------------------------------------------
    # 能力声明：签发、重签、撤销
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_pins(
        pins: Iterable[ScenarioPin | tuple[str, int]],
    ) -> tuple[ScenarioPin, ...]:
        normalized: set[ScenarioPin] = set()
        for pin in pins:
            normalized.add(pin if isinstance(pin, ScenarioPin)
                           else ScenarioPin(pin[0], int(pin[1])))
        if not normalized:
            raise ValueError("能力声明至少钉住一个场景版本")
        return tuple(sorted(normalized, key=lambda p: (p.scenario_id, p.version)))

    def _qualifying_run_ids(self, pin: ScenarioPin, software_version: str) -> tuple[str, ...]:
        """钉住版本上被评审接受的通过运行（按 run_id 排序，天然去重）。"""
        ids: list[str] = []
        for run_id, run in self._runs.items():
            if (run.scenario_id, run.scenario_version) != (pin.scenario_id, pin.version):
                continue
            if run.software_version != software_version:
                continue
            if run.outcome is not RunOutcome.PASSED:
                continue
            verdict = self.active_verdict(run_id)
            if verdict is not None and verdict.decision is VerdictDecision.ACCEPTED:
                ids.append(run_id)
        return tuple(sorted(ids))

    def _uncovered_pins(self, claim: CapabilityClaim) -> tuple[ScenarioPin, ...]:
        return tuple(pin for pin in claim.pins
                     if not self._qualifying_run_ids(pin, claim.software_version))

    def issue_claim(
        self,
        claim_id: str,
        capability: str,
        software_version: str,
        pins: Iterable[ScenarioPin | tuple[str, int]],
        *,
        batch_id: str | None = None,
        at: str,
        by: str,
    ) -> CapabilityClaim:
        """签发能力声明：每个钉住的场景版本都必须有已接受的通过运行。"""
        if claim_id in self._claims:
            raise ConflictError(f"能力声明已存在: {claim_id}")
        normalized = self._normalize_pins(pins)
        for pin in normalized:
            self._require_scenario(pin.scenario_id, pin.version)
        if batch_id is not None:
            batch = self._require_batch(batch_id)
            if batch.status is not BatchStatus.OPEN:
                raise StateError(f"批次 {batch_id} 已冻结，不能加入新声明")
        claim = CapabilityClaim(
            claim_id=claim_id, capability=capability,
            software_version=software_version, pins=normalized,
            status=ClaimStatus.DRAFT, batch_id=batch_id)
        uncovered = self._uncovered_pins(claim)
        if uncovered:
            missing = ", ".join(f"{p.scenario_id}@v{p.version}" for p in uncovered)
            raise CoverageError(f"以下场景版本缺少已接受的通过运行: {missing}")
        claim = replace(claim, status=ClaimStatus.ISSUED, issued_at=at, issued_by=by)
        self._claims[claim_id] = claim
        self._emit("claim_issued", claim_id, at,
                   capability=capability, software_version=software_version,
                   pins=",".join(f"{p.scenario_id}@v{p.version}" for p in normalized))
        return claim

    def reissue_claim(
        self,
        previous_claim_id: str,
        new_claim_id: str,
        *,
        pins: Iterable[ScenarioPin | tuple[str, int]] | None = None,
        batch_id: str | None = None,
        at: str,
        by: str,
    ) -> CapabilityClaim:
        """对受影响或被退回的声明重新签发；原声明转为 SUPERSEDED。"""
        previous = self._require_claim(previous_claim_id)
        if previous.status not in (ClaimStatus.AFFECTED, ClaimStatus.RETURNED):
            raise StateError("仅受影响或被退回的声明可以重新签发")
        if new_claim_id in self._claims:
            raise ConflictError(f"能力声明已存在: {new_claim_id}")
        normalized = (self._normalize_pins(pins) if pins is not None else previous.pins)
        for pin in normalized:
            self._require_scenario(pin.scenario_id, pin.version)
        if batch_id is not None:
            batch = self._require_batch(batch_id)
            if batch.status is not BatchStatus.OPEN:
                raise StateError(f"批次 {batch_id} 已冻结，不能加入新声明")
        candidate = CapabilityClaim(
            claim_id=new_claim_id, capability=previous.capability,
            software_version=previous.software_version, pins=normalized,
            status=ClaimStatus.DRAFT, batch_id=batch_id,
            supersedes=previous.claim_id)
        uncovered = self._uncovered_pins(candidate)
        if uncovered:
            missing = ", ".join(f"{p.scenario_id}@v{p.version}" for p in uncovered)
            raise CoverageError(f"以下场景版本缺少已接受的通过运行: {missing}")
        issued = replace(candidate, status=ClaimStatus.ISSUED, issued_at=at, issued_by=by)
        self._claims[new_claim_id] = issued
        self._transition_claim(
            previous, ClaimStatus.SUPERSEDED,
            reason=f"由 {new_claim_id} 重新签发", at=at, event="claim_superseded")
        self._emit("claim_reissued", new_claim_id, at, previous=previous.claim_id)
        return issued

    def revoke_claim(self, claim_id: str, *, reason: str, at: str, by: str) -> CapabilityClaim:
        """撤销一项声明的结论。撤销是安全动作，冻结批次内同样允许。"""
        claim = self._require_claim(claim_id)
        if claim.status in (ClaimStatus.REVOKED, ClaimStatus.SUPERSEDED):
            raise StateError(f"声明 {claim_id} 已处于终态 {claim.status.value}，不能撤销")
        return self._transition_claim(claim, ClaimStatus.REVOKED,
                                      reason=reason, at=at, event="claim_revoked")

    def get_claim(self, claim_id: str) -> CapabilityClaim:
        return self._require_claim(claim_id)

    # ------------------------------------------------------------------
    # 发布批次：冻结与退回
    # ------------------------------------------------------------------

    def create_batch(self, batch_id: str, title: str, *, at: str) -> ReleaseBatch:
        if batch_id in self._batches:
            raise ConflictError(f"发布批次已存在: {batch_id}")
        batch = ReleaseBatch(batch_id=batch_id, title=title,
                             status=BatchStatus.OPEN, created_at=at)
        self._batches[batch_id] = batch
        self._emit("batch_created", batch_id, at, title=title)
        return batch

    def freeze_batch(self, batch_id: str, *, at: str, by: str) -> ReleaseBatch:
        """冻结批次：之后不能加入新声明，也不能退回局部场景。"""
        batch = self._require_batch(batch_id)
        if batch.status is not BatchStatus.OPEN:
            raise StateError(f"批次 {batch_id} 已冻结")
        frozen = replace(batch, status=BatchStatus.FROZEN, frozen_at=at, frozen_by=by)
        self._batches[batch_id] = frozen
        self._emit("batch_frozen", batch_id, at, by=by)
        return frozen

    def return_scenarios(
        self,
        batch_id: str,
        scenario_ids: Iterable[str],
        *,
        reason: str,
        at: str,
        by: str,
    ) -> tuple[str, ...]:
        """退回批次内覆盖指定场景的已签发声明；返回被退回的声明标识。"""
        batch = self._require_batch(batch_id)
        if batch.status is not BatchStatus.OPEN:
            raise StateError(f"批次 {batch_id} 已冻结，不能退回场景")
        targets = sorted(set(scenario_ids))
        if not targets:
            raise ValueError("退回至少需要一个场景标识")
        returned: list[str] = []
        for claim_id in sorted(self._claims):
            claim = self._claims[claim_id]
            if claim.batch_id != batch_id or claim.status is not ClaimStatus.ISSUED:
                continue
            hit = tuple(s for s in targets
                        if any(pin.scenario_id == s for pin in claim.pins))
            if hit:
                self._transition_claim(
                    claim, ClaimStatus.RETURNED, reason=reason, at=at,
                    event="claim_returned", returned_scenarios=hit)
                returned.append(claim_id)
        self._emit("batch_scenarios_returned", batch_id, at,
                   scenarios=",".join(targets), claims=",".join(returned), by=by)
        return tuple(returned)

    def get_batch(self, batch_id: str) -> ReleaseBatch:
        return self._require_batch(batch_id)

    # ------------------------------------------------------------------
    # 覆盖计算与双向追溯
    # ------------------------------------------------------------------

    def coverage_report(self, claim_id: str) -> CoverageReport:
        """声明的覆盖报告：每个钉住版本对应的合格运行集合（去重后）。"""
        claim = self._require_claim(claim_id)
        pins = tuple(
            PinCoverage(pin=pin,
                        qualifying_runs=self._qualifying_run_ids(pin, claim.software_version),
                        covered=bool(self._qualifying_run_ids(pin, claim.software_version)))
            for pin in claim.pins)
        covered = sum(1 for p in pins if p.covered)
        return CoverageReport(claim_id=claim_id, pins=pins, covered=covered,
                              total=len(pins), complete=covered == len(pins))

    def trace_claim(self, claim_id: str) -> dict:
        """正向追溯：能力声明 → 场景版本 → 运行 → 证据。"""
        claim = self._require_claim(claim_id)
        scenarios: list[dict] = []
        run_ids: list[str] = []
        for pin in claim.pins:
            definition = self._require_scenario(pin.scenario_id, pin.version)
            qualifying = self._qualifying_run_ids(pin, claim.software_version)
            run_ids.extend(qualifying)
            scenarios.append({
                "scenario_id": pin.scenario_id,
                "version": pin.version,
                "content_digest": definition.content_digest(),
                "qualifying_runs": list(qualifying),
            })
        evidence = sorted({e.evidence_id for rid in run_ids
                           for e in self._runs[rid].evidence})
        return {
            "claim_id": claim.claim_id,
            "capability": claim.capability,
            "software_version": claim.software_version,
            "status": claim.status.value,
            "status_reason": claim.status_reason,
            "batch_id": claim.batch_id,
            "issued_at": claim.issued_at,
            "issued_by": claim.issued_by,
            "supersedes": claim.supersedes,
            "scenarios": scenarios,
            "runs": sorted(run_ids),
            "evidence": evidence,
        }

    def trace_scenario(self, scenario_id: str, version: int | None = None) -> dict:
        """反向追溯：场景版本 → 绑定它的运行与钉住它的声明。"""
        versions = (self.list_versions(scenario_id) if version is None
                    else (self._require_scenario(scenario_id, version).version,))
        out_versions: list[dict] = []
        for v in versions:
            definition = self._scenarios[(scenario_id, v)]
            runs = sorted(rid for rid, r in self._runs.items()
                          if (r.scenario_id, r.scenario_version) == (scenario_id, v))
            claims = sorted(c.claim_id for c in self._claims.values()
                            if any(p.scenario_id == scenario_id and p.version == v
                                   for p in c.pins))
            out_versions.append({
                "version": v,
                "parent_version": definition.parent_version,
                "content_digest": definition.content_digest(),
                "change_reason": definition.change_reason,
                "runs": runs,
                "claims": claims,
            })
        return {"scenario_id": scenario_id, "versions": out_versions}

    def trace_run(self, run_id: str) -> dict:
        """反向追溯：运行 → 评审结论、重跑链与由它支撑的声明。"""
        run = self._require_run(run_id)
        verdict = self.active_verdict(run_id)
        supported = sorted(
            c.claim_id for c in self._claims.values()
            if c.software_version == run.software_version
            and run_id in self._qualifying_run_ids_for_claim(c))
        reruns = sorted(rid for rid, r in self._runs.items() if r.rerun_of == run_id)
        return {
            "run_id": run.run_id,
            "scenario": f"{run.scenario_id}@v{run.scenario_version}",
            "software_version": run.software_version,
            "outcome": run.outcome.value,
            "input_digest": run.input_digest,
            "evidence": [e.evidence_id for e in run.evidence],
            "verdict": (None if verdict is None else {
                "verdict_id": verdict.verdict_id,
                "decision": verdict.decision.value,
                "reviewer": verdict.reviewer,
            }),
            "rerun_of": run.rerun_of,
            "reruns": reruns,
            "supported_claims": supported,
        }

    def _qualifying_run_ids_for_claim(self, claim: CapabilityClaim) -> tuple[str, ...]:
        ids: set[str] = set()
        for pin in claim.pins:
            ids.update(self._qualifying_run_ids(pin, claim.software_version))
        return tuple(sorted(ids))

    def trace_evidence(self, evidence_id: str) -> dict:
        """反向追溯：证据 → 所属运行 → 由该运行支撑的声明。"""
        run_ids = sorted(rid for rid, r in self._runs.items()
                         if any(e.evidence_id == evidence_id for e in r.evidence))
        if not run_ids:
            raise NotFoundError(f"证据不存在: {evidence_id}")
        claims = sorted({cid for rid in run_ids
                         for cid in self.trace_run(rid)["supported_claims"]})
        return {"evidence_id": evidence_id, "runs": run_ids, "claims": claims}

    # ------------------------------------------------------------------
    # 审计与确定性快照
    # ------------------------------------------------------------------

    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def snapshot(self) -> dict:
        """全量确定性状态快照；同一操作序列重放后快照必然相同。"""
        return {
            "scenarios": [
                {"scenario_id": d.scenario_id, "version": d.version,
                 "parent_version": d.parent_version,
                 "digest": d.content_digest(),
                 "change_reason": d.change_reason}
                for _, d in sorted(self._scenarios.items())
            ],
            "runs": [
                {"run_id": r.run_id,
                 "scenario": f"{r.scenario_id}@v{r.scenario_version}",
                 "software_version": r.software_version,
                 "outcome": r.outcome.value,
                 "input_digest": r.input_digest,
                 "rerun_of": r.rerun_of,
                 "evidence": [e.evidence_id for e in r.evidence],
                 "verdict": self._verdict_by_run.get(r.run_id)}
                for r in sorted(self._runs.values(), key=lambda x: x.run_id)
            ],
            "verdicts": [
                {"verdict_id": v.verdict_id, "run_id": v.run_id,
                 "decision": v.decision.value,
                 "revoked": v.verdict_id in self._revoked_verdicts}
                for v in sorted(self._verdicts.values(), key=lambda x: x.verdict_id)
            ],
            "claims": [
                {"claim_id": c.claim_id, "capability": c.capability,
                 "status": c.status.value,
                 "pins": [f"{p.scenario_id}@v{p.version}" for p in c.pins],
                 "batch_id": c.batch_id, "supersedes": c.supersedes,
                 "status_reason": c.status_reason}
                for c in sorted(self._claims.values(), key=lambda x: x.claim_id)
            ],
            "batches": [
                {"batch_id": b.batch_id, "status": b.status.value}
                for b in sorted(self._batches.values(), key=lambda x: x.batch_id)
            ],
            "events": [
                {"seq": e.seq, "kind": e.kind, "subject": e.subject,
                 "detail": list(e.detail), "at": e.at}
                for e in self._events
            ],
        }
