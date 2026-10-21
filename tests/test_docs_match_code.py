"""ドキュメント（README・OUTPUT_SPEC・setup）と実際のコード・設定のずれを機械で見つける."""

from __future__ import annotations

import re
import shlex
import tomllib
import unittest
from pathlib import Path

from devtools_common.cli import ExitCode
from test_input_robustness import run

from field_validation import cli as fv_cli
from review_harness import checks
from review_harness import cli as rh_cli

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
OUTPUT_SPEC = (ROOT / "fieldcheck/OUTPUT_SPEC.md").read_text(encoding="utf-8")


def requires_python() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    m = re.fullmatch(r">=\s*(\d+\.\d+)", pyproject["project"]["requires-python"])
    assert m, pyproject["project"]["requires-python"]
    return m.group(1)


def exit_code_rows(md: str, heading: str) -> list[int]:
    """`## 終了コード` の表の 1 列目（コード）."""
    section = md.split(heading, 1)[1].split("\n## ", 1)[0]
    return [int(m.group(1)) for m in re.finditer(r"^\|\s*(\d+)\s*\|", section, re.M)]


class DocsMatchCodeTest(unittest.TestCase):
    def test_python_version_matches_requires_python(self):
        v = requires_python()
        self.assertIn(f"Python {v} 以上", README)
        self.assertIn(f"Python {v}", (ROOT / "setup.cmd").read_bytes().decode("cp932"))
        major, minor = v.split(".")
        self.assertIn(f"sys.version_info < ({major}, {minor})", (ROOT / "setup.sh").read_text(encoding="utf-8"))
        self.assertIn(f"Python {v} 以上", (ROOT / "setup.sh").read_text(encoding="utf-8"))
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r'python-version: "([\d.]+)"', ci)), {v})

    def test_check_count_matches_catalog(self):
        n = len(checks.load(ROOT / "config/checks.yaml").checks)
        m = re.search(r"config/checks\.yaml\)（(\d+) 観点）", README)
        self.assertIsNotNone(m, "README に観点の数がありません")
        self.assertEqual(int(m.group(1)), n)

    def test_exit_code_tables_match_constants(self):
        codes = sorted(int(c) for c in ExitCode)
        self.assertEqual(exit_code_rows(README, "## 終了コード"), codes)
        self.assertEqual(exit_code_rows(OUTPUT_SPEC, "## 終了コード"), codes)
        for mod in (fv_cli, rh_cli):
            doc = mod.__doc__ or ""
            for c in codes:
                self.assertRegex(doc, rf"\b{c} ", f"{mod.__name__} の docstring に終了コード {c} がありません")

    def test_readme_fieldcheck_examples_run(self):
        """README の `fieldcheck validate ...` の例が（引数・表の名前の誤りの終了コード 2 にならずに）動く."""
        block = README.split("## fieldcheck", 1)[1].split("```bash", 1)[1].split("```", 1)[0]
        cmds = [line.split("#", 1)[0].strip() for line in block.splitlines() if line.startswith("fieldcheck validate")]
        self.assertTrue(cmds)
        for cmd in cmds:
            args = [a if not a.startswith("fieldcheck/") else str(ROOT / a) for a in shlex.split(cmd)[1:]]
            with self.subTest(cmd=cmd):
                rc, _, err = run(fv_cli.main, *args, "-f", "json")
                self.assertIn(rc, (ExitCode.OK, ExitCode.FINDINGS), err)

    def test_documented_commands_exist(self):
        """README・docs に書いた harness のサブコマンドが実在する."""
        sub = next(a for a in rh_cli.build_parser()._actions if a.dest == "command")
        known = set(sub.choices)  # type: ignore[union-attr]
        docs = [README, *(p.read_text(encoding="utf-8") for p in (ROOT / "docs").glob("*.md"))]
        used = {m.group(1) for d in docs for m in re.finditer(r"`harness ([a-z-]+)", d)}
        self.assertTrue(used)
        self.assertLessEqual(used, known)
