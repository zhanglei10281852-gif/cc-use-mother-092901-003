import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from scenario_certification.contracts import RunOutcome, ScenarioVersion, TestRun


scenario = ScenarioVersion("SC-12", 4, "urban-junction", ("rain", "pedestrian"))
run = TestRun("RUN-1", scenario, "sw-2026.10", RunOutcome.PASSED)
print(json.dumps({"scenario": run.scenario.scenario_id, "version": run.scenario.version, "outcome": run.outcome.value}, ensure_ascii=False))
