"""指紋 v2 と history.jsonl の結び付けを devtools-common の共通部品（fingerprint_v2・resolve_history_v2）に寄せた後も、
office-review の振る舞い（指紋の値・旧形式との互換）が変わらないことの確認.

期待値は、置き換える前の office-review 自前の実装（review_harness.results.fingerprint と history.resolve）で求めた値.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from devtools_common import review_results as rr

from review_harness import history, results

ROOT = Path(__file__).resolve().parent.parent
REVIEW = "reviews/2026-10-02_order-api_result.md"
# No 4（dba・API-007）と同じ箇所・同じ本文を、別のロール（infra）が出した行（旧指紋が同じになる）
DUP = (
    "| 7 | API-007 | WANT | infra | 3. 処理 1 | 参照テーブルと検索条件・使うインデックスが書かれていない"
    " | 3. 処理 1 | DB を使う API のため該当しない |"
)


class UsesCommonPartsTest(unittest.TestCase):
    def test_no_own_fingerprint_or_resolve_implementation(self):
        self.assertIs(results.fingerprint, rr.fingerprint_v2)
        self.assertIs(history.resolve, rr.resolve_history_v2)
        src = (ROOT / "src/review_harness/results.py").read_text(encoding="utf-8")
        self.assertNotIn("hashlib", src)
        self.assertFalse(hasattr(history, "_upgrade"))


class FingerprintValueTest(unittest.TestCase):
    """置き換える前と同じ値（history.jsonl の fingerprint_v2・triage.json のキーが変わらない）."""

    def test_known_values(self):
        args = ("inputs/api/order-api.md", "3. 入力", "必須項目が決まっていない", "tester", "API-003")
        self.assertEqual(results.fingerprint(*args), "fp:13fe25e6d57d6d3d")
        # normalize（NFKC・空白・大文字小文字）も同じ
        spaced = (" Inputs/API/order-api.md", "3.  入力 ", "必須項目が決まっていない", "Tester", "api-003")
        self.assertEqual(results.fingerprint(*spaced), "fp:13fe25e6d57d6d3d")

    def test_item_properties(self):
        it = results.ReviewItem(
            file="2026-10-02_x_result.md",
            target="",
            doc_type="api",
            reviewed_at="2026-10-02",
            no=1,
            check_id="NEW",
            severity="Q",
            role="ops",
            location="全体",
            finding="ｆｕｌｌ　width",
            basis="b",
            invalid_check="c",
        )
        # target が空ならファイル名で作る
        self.assertEqual(it.fingerprint, "fp:cce3edd7c12a0617")
        self.assertEqual(it.fingerprint_v2, it.fingerprint)
        self.assertEqual(it.fingerprint_v1, "fp:d027862fa24f2718")
        self.assertEqual(it.fingerprint_v1, rr.ReviewItem(**vars(it)).fingerprint)  # 共通部品の旧指紋と同じ

    def test_committed_reviews(self):
        for it in results.load_all(ROOT / "reviews"):
            with self.subTest(no=it.no):
                self.assertEqual(
                    it.fingerprint,
                    rr.fingerprint_v2(it.target or it.file, it.location, it.finding, it.role, it.check_id),
                )
                self.assertEqual(it.fingerprint_v1, rr.fingerprint(it.target or it.file, it.location, it.finding))


class MixedHistoryResolveTest(unittest.TestCase):
    """v2・旧指紋だけ・key だけ（もっと古い形式）の行が混ざった history.jsonl の結び付け."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "reviews", self.tmp / "reviews")
        p = self.tmp / REVIEW
        p.write_text(p.read_text(encoding="utf-8").rstrip("\n") + "\n" + DUP + "\n", encoding="utf-8")
        self.items = results.load_all(self.tmp / "reviews")
        self.by_no = {i.no: i for i in self.items}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rec(self, no: int, decision: str, *, form: str, **over) -> dict:
        it = self.by_no[no]
        r = {"key": it.key, "decision": decision, "check_id": it.check_id, "role": it.role, "finding": it.finding}
        if form in ("v1", "v2"):
            r["fingerprint"] = it.fingerprint_v1
        if form == "v2":
            r["fingerprint_v2"] = it.fingerprint
        return {**r, **over}

    def test_same_result_as_before(self):
        i1, i4, i5, i7 = (self.by_no[n] for n in (1, 4, 5, 7))
        self.assertEqual(i4.fingerprint_v1, i7.fingerprint_v1)
        records = [
            self.rec(1, "adopted", form="v2"),
            self.rec(4, "rejected", form="v1"),  # 旧指紋が同じ指摘が 2 つ → 記録したロール・観点で No 4 に決まる
            self.rec(4, "adopted", form="v1", role="ops"),  # ロールが一致しない → 決められず旧指紋のまま
            self.rec(2, "rejected", form="v1", fingerprint="fp:0000000000000000"),  # いまの指摘に無い旧指紋
            self.rec(5, "adopted", form="key"),  # key だけ・本文と check_id が一致 → No 5 に引き継ぐ
            self.rec(6, "adopted", form="key", finding="別の指摘"),  # key だけ・本文が違う → 孤立
            self.rec(2, "rejected", form="v2"),
            self.rec(2, "adopted", form="v2"),  # 同じ指紋は後の行が勝つ
        ]
        res = history.resolve(records, self.items)
        got = {fp: (r["decision"], r["fingerprint"]) for fp, r in res.decisions.items()}
        self.assertEqual(
            got,
            {
                i1.fingerprint: ("adopted", i1.fingerprint),
                i4.fingerprint: ("rejected", i4.fingerprint),
                i4.fingerprint_v1: ("adopted", i4.fingerprint_v1),
                "fp:0000000000000000": ("rejected", "fp:0000000000000000"),
                i5.fingerprint: ("adopted", i5.fingerprint),
                self.by_no[2].fingerprint: ("adopted", self.by_no[2].fingerprint),
            },
        )
        self.assertNotIn(i7.fingerprint, res.decisions)
        self.assertEqual([r["key"] for r in res.orphans], [self.by_no[6].key])
        self.assertEqual(set(res.all_records()), {*res.decisions, "legacy:" + self.by_no[6].key})
        # 入力の行は書き換えない
        self.assertNotIn("fingerprint", records[4])
        self.assertEqual(records[1]["fingerprint"], i4.fingerprint_v1)

    def test_v1_only_single_candidate_and_v2_written_by_append(self):
        path = self.tmp / "data/history.jsonl"
        path.parent.mkdir()
        path.write_text(json.dumps(self.rec(1, "rejected", form="v1"), ensure_ascii=False) + "\n", encoding="utf-8")
        history.append(path, {self.by_no[1].fingerprint: "adopted"}, self.items, date="2026-10-08")
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[1]["fingerprint"], self.by_no[1].fingerprint_v1)
        self.assertEqual(lines[1]["fingerprint_v2"], self.by_no[1].fingerprint)
        res = history.load(path, self.items)
        self.assertEqual(list(res.decisions), [self.by_no[1].fingerprint])
        self.assertEqual(res.decisions[self.by_no[1].fingerprint]["decision"], "adopted")

    def test_broken_lines_are_skipped_with_warning(self):
        path = self.tmp / "history.jsonl"
        good = self.rec(1, "adopted", form="v2")
        lines = [
            {**good, "decision": ["adopted"]},  # 文字列でない（以前の共通部品は TypeError）
            {"decision": "adopted", "check_id": "API-001"},  # 結び付ける識別子が無い（以前は KeyError）
            good,
        ]
        path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        warnings: list[str] = []
        recs = history.load_records(path, warnings.append)
        self.assertEqual(recs, [good])
        self.assertEqual(
            [w.split("（")[0] for w in warnings],
            ["警告: history.jsonl:1 を飛ばしました", "警告: history.jsonl:2 を飛ばしました"],
        )


if __name__ == "__main__":
    unittest.main()
