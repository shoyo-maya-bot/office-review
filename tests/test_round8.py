"""第 8 回: 観点（checks.yaml）の文と機械チェックのずれ・convert の上書き・triage.json の保存先の案内."""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devtools_common.cli import UsageError, run_main

from review_harness import checks, cli, dashboard, precheck

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
# 見出しの machine が「何でも通る」パターンになっていないかを確かめるための、どの設計書にもありがちな見出し
GENERIC_HEADINGS = ["概要", "1. 概要", "はじめに", "一覧", "処理", "3. 処理", "改版履歴", "目次"]


def harness(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_main(cli.main, ["--root", str(root), *args])
    return rc, out.getvalue(), err.getvalue()


class MachineMatchesViewpointTest(unittest.TestCase):
    """観点の文（viewpoint. SSOT）と machine（precheck が確かめる部分）がずれていない."""

    def test_machine_words_appear_in_viewpoint(self):
        catalog = checks.load(ROOT / "config/checks.yaml")
        machines = [c for c in catalog.checks if c.machine]
        self.assertTrue(machines)
        for c in machines:
            m, vp = c.machine, c.viewpoint
            with self.subTest(check=c.id):
                if m["kind"] == "front_matter":
                    for key in m["keys"]:
                        alts = precheck.key_alternatives(key)
                        self.assertTrue(any(a in vp for a in alts), f"{c.id}: {key} が viewpoint にありません: {vp}")
                elif m["kind"] == "table_columns":
                    for col in m["columns"]:
                        self.assertIn(precheck._norm(col), precheck._norm(vp), f"{c.id}: 列 {col} が viewpoint にない")
                elif m["kind"] == "heading":
                    rx = re.compile(m["pattern"])
                    hits = [h for h in GENERIC_HEADINGS if rx.search(h)]
                    self.assertEqual(hits, [], f"{c.id}: pattern がありがちな見出しにも当たります")

    def test_all001_checks_id_version_status(self):
        c = checks.load(ROOT / "config/checks.yaml").by_id()["ALL-001"]
        for word in ("id", "版数", "status"):
            self.assertIn(word, c.viewpoint)
        keys = [precheck.key_alternatives(k) for k in c.machine["keys"]]
        self.assertEqual(keys, [["id"], ["version", "版数"], ["status"]])


class PrecheckRound8Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ["config", "docs", "inputs", "reviews"]:
            shutil.copytree(ROOT / d, self.tmp / d)
        self.catalog = checks.load(self.tmp / "config/checks.yaml")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rules(self, text: str, doc_type: str = "api") -> dict[str, str]:
        doc = self.tmp / "doc.md"
        doc.write_text(text, encoding="utf-8")
        return {f.rule_id: f.message for f in precheck.run(doc, doc_type, self.catalog)}

    TABLES = "| API ID | メソッド | パス | 認証 |\n|---|---|---|---|\n| A1 | GET | /x | 要 |\n\n" + (
        "| 項目 | 必須 | 形式 | 桁 |\n|---|---|---|---|\n| a | ○ | 数値 | 1 |\n"
    )

    def test_id_and_title_only_is_not_enough(self):
        """以前は id・title があれば ALL-001 を通していた（観点は id・版数・status）."""
        got = self.rules('---\nid: "D"\ntitle: "T"\n---\n# x\n' + self.TABLES)
        self.assertEqual(list(got), ["ALL-001"])
        self.assertIn("version か 版数", got["ALL-001"])
        self.assertIn("status", got["ALL-001"])

    def test_version_or_japanese_key(self):
        for fm in ('id: "D"\nversion: "1.0"\nstatus: review', 'id: "D"\n版数: "1.0"\nstatus: draft'):
            with self.subTest(fm=fm):
                self.assertEqual(self.rules(f"---\n{fm}\n---\n# x\n" + self.TABLES), {})

    def test_api001_needs_table_not_just_heading(self):
        """以前は見出しに「概要」を含めば API-001 を通していた."""
        fm = '---\nid: "D"\nversion: "1"\nstatus: review\n---\n'
        got = self.rules(fm + "# x\n## 1. 概要\n\nAPI の一覧は別紙。\n\n## 一覧\n")
        self.assertIn("API-001", got)
        self.assertNotIn("API-001", self.rules(fm + "# x\n" + self.TABLES))

    def test_sample_passes(self):
        self.assertEqual(precheck.run(self.tmp / "inputs/api/order-api.md", "api", self.catalog), [])

    def test_alternative_key_must_not_be_empty(self):
        self.assertIn(
            "keys の「a|b」の両側には空でないキー名",
            " ".join(checks.machine_errors({"kind": "front_matter", "keys": ["a|"]})),
        )


class ConvertRound8Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "config", self.tmp / "config")
        (self.tmp / "inputs/api").mkdir(parents=True)
        self.src = self.tmp / "spec.xlsx"
        self.src.write_bytes(b"dummy")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def convert(self, calls: list):
        def fake_run(cmd, check=False):
            calls.append(cmd)
            rep = Path(cmd[cmd.index("-o") + 1])
            rep.write_text(json.dumps({"findings": []}), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch("shutil.which", return_value="doc2md"), mock.patch("subprocess.run", fake_run):
            return harness(self.tmp, "convert", str(self.src), "--type", "api")

    def test_report_named_with_extension(self):
        calls: list = []
        rc, out, err = self.convert(calls)
        self.assertEqual(rc, 0, err)
        rep = Path(calls[0][calls[0].index("-o") + 1])
        self.assertEqual(rep.name, "spec.xlsx.json")
        self.assertIn("data/convert-reports/api/spec.xlsx.json", out)

    def test_refuses_to_overwrite_output_from_other_source(self):
        md = self.tmp / "inputs/api/spec.md"
        md.write_text('---\ntitle: "spec"\nsource: "spec.docx"\n---\n\n# spec\n', encoding="utf-8")
        calls: list = []
        rc, _, err = self.convert(calls)
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [])  # doc2md を起動しない
        self.assertIn("spec.docx", err)
        self.assertIn("上書きしません", err)

    def test_same_source_reconverts(self):
        (self.tmp / "inputs/api/spec.md").write_text('﻿---\nsource: "spec.xlsx"\nid: "DOC-1"\n---\n', encoding="utf-8")
        calls: list = []
        self.assertEqual(self.convert(calls)[0], 0)
        self.assertEqual(len(calls), 1)

    def test_refuse_helper_without_front_matter(self):
        md = self.tmp / "inputs/api/spec.md"
        md.write_text("# 人が書いた Markdown\n", encoding="utf-8")
        cli._refuse_other_source(md, self.src, self.tmp)  # source が無ければ止めない（以前と同じ）
        md.write_text('---\nsource: "spec.docx"\n---\n', encoding="utf-8")
        with self.assertRaises(UsageError):
            cli._refuse_other_source(md, self.src, self.tmp)


class TriageGuidanceTest(unittest.TestCase):
    def test_dashboard_and_readme_tell_where_triage_json_goes(self):
        html = dashboard.render([], checks.load(ROOT / "config/checks.yaml"), {})
        self.assertIn("ダウンロードフォルダ", html)
        self.assertIn("ダウンロードフォルダ", README)
        workflow = (ROOT / "docs/WORKFLOW.md").read_text(encoding="utf-8")
        self.assertIn("ダウンロードフォルダ", workflow)

    def test_dashboard_command_prints_next_step(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            for d in ["config", "docs", "inputs", "reviews"]:
                shutil.copytree(ROOT / d, tmp / d)
            (tmp / "data").mkdir()
            rc, out, _ = harness(tmp, "dashboard")
            self.assertEqual(rc, 0)
            self.assertIn("ダウンロードフォルダ", out)
            self.assertIn("harness triage", out)
        finally:
            shutil.rmtree(tmp)


class WindowsCiTest(unittest.TestCase):
    """Windows の CI で、利用者と同じ setup.cmd と lint --fail-if-empty を確かめている."""

    def windows_job(self) -> str:
        return CI.split("test-windows:", 1)[1].split("\n  convert:", 1)[0]

    def test_windows_job_runs_setup_cmd_and_lint(self):
        job = self.windows_job()
        self.assertIn("setup.cmd --no-convert", job)
        self.assertIn("harness lint --fail-if-empty", job)
        self.assertIn("harness checklists --check", job)

    def test_setup_cmd_checks_python_version_and_skips_pause_on_ci(self):
        text = (ROOT / "setup.cmd").read_bytes().decode("cp932")
        self.assertIn("sys.version_info < (3, 12)", text)
        self.assertNotRegex(text, r"(?m)^pause\r?$")
        self.assertIn("if not defined CI pause", text)
        self.assertIn("SETUP_PYTHON", text)


if __name__ == "__main__":
    unittest.main()
