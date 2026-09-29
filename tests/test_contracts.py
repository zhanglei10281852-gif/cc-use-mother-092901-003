import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from scenario_certification.contracts import RunOutcome, ScenarioVersion, TestRun


class ScenarioContractTests(unittest.TestCase):
    def test_run_binds_exact_scenario_version(self):
        scenario = ScenarioVersion("SC-1", 2, "expressway", ("fog",))
        run = TestRun("R-1", scenario, "sw-8", RunOutcome.INCONCLUSIVE)
        self.assertEqual((run.scenario.version, run.outcome.value), (2, "inconclusive"))

    def test_zero_version_is_rejected(self):
        with self.assertRaises(ValueError):
            ScenarioVersion("SC-2", 0, "urban", ())


if __name__ == "__main__":
    unittest.main()
