"""CI はテストのカバレッジの下限を確かめる（pyproject.toml の [tool.coverage.report] fail_under）.

下限が無いと、テストの無いコードが増えても CI では気づけない.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CoverageFloorTest(unittest.TestCase):
    def test_floor_is_configured(self):
        conf = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]
        self.assertEqual(conf["run"]["source"], ["src"])
        self.assertGreaterEqual(conf["report"]["fail_under"], 80)

    def test_ci_runs_tests_under_coverage_and_checks_the_floor(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("coverage run -m pytest", ci)
        self.assertIn("coverage report", ci)
        self.assertRegex(ci, r'pip install "coverage==\d+\.\d+\.\d+"')


if __name__ == "__main__":
    unittest.main()
