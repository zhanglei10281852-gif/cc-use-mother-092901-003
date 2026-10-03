"""自动驾驶场景核证领域包。"""

from .backend import CertificationBackend, RevisionImpact
from .contracts import RunOutcome, ScenarioVersion, TestRun
from .errors import ConflictError, DomainError, NotFoundError, StateError, ValidationError
from .model import (
    BatchState,
    Capability,
    CapabilityClaim,
    ClaimStatus,
    ConclusionState,
    Evidence,
    ExpectedBehavior,
    FaultInjection,
    ReleaseBatch,
    ReviewConclusion,
    RoadEnvironment,
    RunRecord,
    ScenarioContent,
    ScenarioRef,
    ScenarioRevision,
    TrafficParticipant,
    Verdict,
    VisibilityAndFriction,
)
from .services import CoverageReport, RunIngestion

__all__ = [
    "CertificationBackend",
    "RevisionImpact",
    "RunOutcome",
    "ScenarioVersion",
    "TestRun",
    "DomainError",
    "NotFoundError",
    "ConflictError",
    "StateError",
    "ValidationError",
    "BatchState",
    "Capability",
    "CapabilityClaim",
    "ClaimStatus",
    "ConclusionState",
    "Evidence",
    "ExpectedBehavior",
    "FaultInjection",
    "ReleaseBatch",
    "ReviewConclusion",
    "RoadEnvironment",
    "RunRecord",
    "ScenarioContent",
    "ScenarioRef",
    "ScenarioRevision",
    "TrafficParticipant",
    "Verdict",
    "VisibilityAndFriction",
    "CoverageReport",
    "RunIngestion",
]
