"""场景定义与测试运行使用的数据契约。"""

from dataclasses import dataclass
from enum import StrEnum


class RunOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ScenarioVersion:
    scenario_id: str
    version: int
    road_context: str
    parameters: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("场景版本必须为正整数")


@dataclass(frozen=True)
class TestRun:
    run_id: str
    scenario: ScenarioVersion
    software_version: str
    outcome: RunOutcome
