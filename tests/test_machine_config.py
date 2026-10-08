"""config/checks.yaml の machine を読み込み時に検証する（未知の kind・必須のキーの欠落は設定の誤り＝終了コード 2）.

以前は未知の kind を黙って飛ばし、heading の pattern が無いと precheck の途中で KeyError（終了コード 3）だった.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from devtools_common.cli import UsageError, run_main

from review_harness import checks, cli

ROOT = Path(__file__).resolve().parent.parent
DOC = "inputs/api/order-api.md"
ORIGINAL = 'machine: { kind: heading, pattern: "概要|API 一覧|一覧" }'


def harness(root: Path, *args: str) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = run_main(cli.main, ["--root", str(root), *args])
    return rc, err.getvalue()


class MachineConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ["config", "docs", "inputs", "reviews"]:
            shutil.copytree(ROOT / d, self.tmp / d)
        self.yaml = self.tmp / "config/checks.yaml"
        self.original = self.yaml.read_text(encoding="utf-8")
        self.assertIn(ORIGINAL, self.original)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def replace_machine(self, new: str) -> None:
        self.yaml.write_text(self.original.replace(ORIGINAL, new, 1), encoding="utf-8")

    def test_committed_config_is_valid(self):
        catalog = checks.load(ROOT / "config/checks.yaml")
        self.assertTrue(any(c.machine for c in catalog.checks))

    def test_bad_machine_is_config_error_on_load(self):
        cases = {
            "machine: { kind: headings, pattern: 概要 }": "kind 'headings' は使えません",
            "machine: { kind: heading }": "kind heading には pattern が必要です",
            "machine: { kind: heading, regex: 概要 }": "kind heading には pattern が必要です",
            "machine: { kind: table_columns }": "kind table_columns には columns が必要です",
            "machine: { kind: front_matter }": "kind front_matter には keys が必要です",
            "machine: { kind: front_matter, keys: id }": "keys は空でない文字列の一覧",
            "machine: { kind: table_columns, columns: [] }": "columns は空でない文字列の一覧",
            'machine: { kind: heading, pattern: "(" }': "pattern が正規表現として不正です",
            "machine: { pattern: 概要 }": "kind がありません",
            "machine: heading": "kind を持つ mapping にしてください",
        }
        for machine, message in cases.items():
            with self.subTest(machine=machine):
                self.replace_machine(machine)
                with self.assertRaises(UsageError) as cm:
                    checks.load(self.yaml)
                self.assertIn("API-001: machine: " + message, str(cm.exception))
                rc, err = harness(self.tmp, "precheck", str(self.tmp / DOC), "--type", "api")
                self.assertEqual(rc, 2, err)
                self.assertIn(message, err)

    def test_other_commands_also_reject_bad_machine(self):
        self.replace_machine("machine: { kind: heading }")
        for args in (["lint"], ["checklists", "--check"]):
            with self.subTest(args=args):
                self.assertEqual(harness(self.tmp, *args)[0], 2)


if __name__ == "__main__":
    unittest.main()
