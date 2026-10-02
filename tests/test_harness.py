"""review_harness のテスト（リポジトリを一時ディレクトリに複製して実行）."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from devtools_common.cli import run_main

from review_harness import checks, cli

ROOT = Path(__file__).resolve().parent.parent
REVIEW = "reviews/2026-10-02_order-api_result.md"


def harness(root: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = run_main(cli.main, ["--root", str(root), *args])
    return rc, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ["config", "docs", "inputs", "reviews"]:
            shutil.copytree(ROOT / d, self.tmp / d)
        (self.tmp / "data").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit(self, rel: str, old: str, new: str) -> None:
        p = self.tmp / rel
        s = p.read_text(encoding="utf-8")
        self.assertIn(old, s)
        p.write_text(s.replace(old, new, 1), encoding="utf-8")

    def rules(self, out: str) -> list[str]:
        return [f["rule_id"] for f in json.loads(out)["findings"]]


class ChecklistTest(Base):
    def test_committed_checklists_are_fresh(self):
        self.assertEqual(harness(ROOT, "checklists", "--check")[0], 0)

    def test_stale_checklist_detected_and_regenerated(self):
        self.edit(
            "config/checks.yaml", "title: 想定件数・応答時間・同時実行数がある", "title: 想定件数と応答時間がある"
        )
        self.assertEqual(harness(self.tmp, "checklists", "--check")[0], 1)
        self.assertEqual(harness(self.tmp, "checklists")[0], 0)
        self.assertIn(
            "想定件数と応答時間がある", (self.tmp / "docs/review-checklists/api-review.md").read_text("utf-8")
        )

    def test_invalid_catalog_is_usage_error(self):
        self.edit("config/checks.yaml", "id: API-002", "id: API-001")
        self.edit(
            "config/checks.yaml",
            "role: dba\n    category: データアクセス",
            "role: tester\n    category: データアクセス",
        )
        rc, _ = harness(self.tmp, "checklists")
        self.assertEqual(rc, 2)
        with self.assertRaises(Exception) as ctx:
            checks.load(self.tmp / "config/checks.yaml")
        self.assertIn("重複", str(ctx.exception))
        self.assertIn("tester", str(ctx.exception))


class PrecheckTest(Base):
    def test_sample_passes_machine_checks(self):
        rc, out = harness(
            self.tmp, "precheck", str(self.tmp / "inputs/api/order-api.md"), "--type", "api", "-f", "json"
        )
        self.assertEqual((rc, self.rules(out)), (0, []))

    def test_missing_front_matter_heading_and_columns(self):
        doc = self.tmp / "inputs/api/bad.md"
        doc.write_text("# 何かの API\n\n| 名前 | 説明 |\n|---|---|\n| a | b |\n", encoding="utf-8")
        rc, out = harness(self.tmp, "precheck", str(doc), "--type", "api", "-f", "json")
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(self.rules(out)), ["ALL-001", "API-001", "API-002"])

    def test_batch_step_heading(self):
        doc = self.tmp / "inputs/batch/job.md"
        doc.write_text('---\nid: "D"\ntitle: "T"\n---\n# JOB\n## 処理\n', encoding="utf-8")
        _, out = harness(self.tmp, "precheck", str(doc), "--type", "batch", "-f", "json")
        self.assertEqual(self.rules(out), ["BAT-003"])
        doc.write_text('---\nid: "D"\ntitle: "T"\n---\n# JOB\n### STEP1 入力チェック\n', encoding="utf-8")
        _, out = harness(self.tmp, "precheck", str(doc), "--type", "batch", "-f", "json")
        self.assertEqual(self.rules(out), [])


class LintTest(Base):
    def test_sample_is_valid(self):
        rc, out = harness(self.tmp, "lint", "-f", "json")
        self.assertEqual((rc, self.rules(out)), (0, []))

    def test_detects_problems(self):
        self.edit(REVIEW, "| 2 | API-003 | MUST |", "| 2 | API-999 | MUST |")
        self.edit(REVIEW, "| 3 | API-006 | MUST |", "| 3 | BAT-001 | HIGH |")
        self.edit(
            REVIEW,
            "| 4 | API-007 | WANT | dba | 3. 処理 1 | 参照テーブルと検索条件・使うインデックスが書かれていない | 3. 処理 1 |",
            "| 4 | API-007 | WANT | dba | 3. 処理 1 | 参照テーブルと検索条件・使うインデックスが書かれていない |  |",
        )
        rc, out = harness(self.tmp, "lint", "-f", "json")
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(self.rules(out)), ["lint.basis", "lint.check_id", "lint.check_type", "lint.severity"])

    def test_missing_table(self):
        (self.tmp / "reviews/x_result.md").write_text(
            "---\ntarget: a\ntype: api\nreviewed_at: 2026-10-02\n---\n# r\n", encoding="utf-8"
        )
        _, out = harness(self.tmp, "lint", "-f", "json")
        self.assertIn("lint.table", self.rules(out))


class TriageTest(Base):
    def triage(self, decisions: dict) -> int:
        f = self.tmp / "triage.json"
        f.write_text(json.dumps(decisions), encoding="utf-8")
        return harness(self.tmp, "triage", str(f))[0]

    def test_flow_and_candidates(self):
        key = "2026-10-02_order-api_result.md#{}"
        self.assertEqual(
            self.triage({key.format(1): "adopted", key.format(5): "adopted", key.format(4): "rejected"}), 0
        )
        self.assertEqual(harness(self.tmp, "accumulate")[0], 0)
        adoption = (self.tmp / "docs/adoption.md").read_text("utf-8")
        self.assertIn("| ALL-003 |", adoption)
        self.assertIn("| API-007 | 参照・更新するテーブルと条件が書かれている | 0 | 1 | 0% |  |", adoption)
        self.assertIn("タイムゾーン", (self.tmp / "docs/check-candidates.md").read_text("utf-8"))

    def test_low_adoption_is_flagged(self):
        lines = []
        for i in range(6):
            lines.append(
                json.dumps(
                    {
                        "key": f"r{i}#1",
                        "decision": "rejected" if i else "adopted",
                        "check_id": "API-005",
                        "severity": "WANT",
                        "role": "infra",
                        "doc_type": "api",
                        "finding": "x",
                        "triaged_at": "2026-10-02",
                    }
                )
            )
        (self.tmp / "data/history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        harness(self.tmp, "accumulate")
        self.assertIn(
            "| API-005 | 想定件数・応答時間・同時実行数がある | 1 | 5 | 17% | ⚠ |",
            (self.tmp / "docs/adoption.md").read_text("utf-8"),
        )

    def test_unknown_key_or_decision_is_usage_error(self):
        self.assertEqual(self.triage({"nope#1": "adopted"}), 2)
        self.assertEqual(self.triage({"2026-10-02_order-api_result.md#1": "maybe"}), 2)


class DashboardTest(Base):
    def test_html_escapes_and_contains_items(self):
        self.edit(REVIEW, "「適宜ページングする」では", "<script>alert(1)</script> 「適宜ページングする」では")
        rc, _ = harness(self.tmp, "dashboard")
        self.assertEqual(rc, 0)
        html = (self.tmp / "reviews/dashboard.html").read_text("utf-8")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(html.count('type="radio" name="2026-10-02_order-api_result.md#'), 12)


if __name__ == "__main__":
    unittest.main()
