"""トリアージ結果（採択 / 不採択）の蓄積と、観点ごとの採択率の集計.

- data/history.jsonl に 1 指摘 1 行で追記する。指摘は「指紋」（対象・箇所・指摘の本文・ロール・観点のハッシュ。
  fingerprint_v2）で特定し、同じ指紋は最新で上書き扱い。No（ファイル名#No）は再レビューでずれるので結び付けには使わない
- fingerprint には旧指紋（対象・箇所・指摘の本文だけ）も書く（quality-analytics などの読み手が引く値）。
  fingerprint_v2 の無い行（0.2.0 までに記録した行）は、旧指紋が同じ指摘に結び付ける。旧指紋が同じ指摘が複数あるときは、
  記録したロールと観点が一致するものに結び付ける
- 旧形式の行（fingerprint なし）は、その No にいまある指摘が同じとき（本文と check_id が一致）だけ引き継ぎ、
  違えば「孤立した判定」として報告する（採択率の集計には数える）
- 採択率 = 採択 / (採択 + 不採択)。件数が少ないうちは判断しない（min_samples）
- 採択率が低い観点は「誤指摘が多い = 観点か無効ケースの書き方が悪い」ので見直し候補にする
- NEW（既存の観点に無い）指摘で採択された MUST / WANT は、観点の追加候補にする
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from devtools_common import review_results as rr

from .results import ReviewItem

# 読み込みと結び付けは devtools-common の共通部品で行う（quality-analytics と同じ規則）
DECISIONS = rr.HISTORY_DECISIONS
REQUIRED_KEYS = rr.HISTORY_REQUIRED_KEYS
Resolved = rr.ResolvedHistory


@dataclass
class Stat:
    check_id: str
    adopted: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        return self.adopted + self.rejected

    @property
    def rate(self) -> float | None:
        return self.adopted / self.total if self.total else None


def load_records(path: Path, warn=None) -> list[dict]:
    """data/history.jsonl の全行（書いた順）.

    壊れた行（JSON として読めない・オブジェクトでない・判定や check_id が無い）は、行番号つきで警告して飛ばす
    （1 行の破損で全コマンドが止まらないように）。warn: 警告の出し先（既定は標準エラー）。
    """
    return rr.load_history(path, warn)


def resolve(records: list[dict], items: list[ReviewItem]) -> Resolved:
    """判定を指紋（fingerprint_v2）で引けるようにする. 同じ指紋は後に書いたものが勝つ.

    - fingerprint_v2 がある行: その指紋の判定
    - fingerprint（旧指紋）だけの行: 旧指紋が同じいまの指摘の指紋に引き継ぐ（複数あればロールと観点が一致するもの）。
      いまの指摘に無ければ旧指紋のまま残す（採択率の集計には数える）
    - どちらも無い行（もっと古い形式。key = ファイル名#No）: key の No にいまある指摘が同じ指摘のときだけ引き継ぎ、
      違えば孤立した判定として返す
    """
    items = list(items)
    by_v1: dict[str, list[ReviewItem]] = {}
    for it in items:
        by_v1.setdefault(it.fingerprint_v1, []).append(it)
    upgraded = []
    for rec in records:
        if not rec.get("fingerprint_v2") and rec.get("fingerprint"):
            fp = _upgrade(rec, by_v1.get(rec["fingerprint"], []))
            rec = {**rec, "fingerprint": fp} if fp else rec
        elif rec.get("fingerprint_v2"):
            rec = {**rec, "fingerprint": rec["fingerprint_v2"]}
        upgraded.append(rec)
    return rr.resolve_history(upgraded, items)  # 旧形式（fingerprint なし）の結び付けは共通部品と同じ規則


def _upgrade(rec: dict, candidates: list[ReviewItem]) -> str | None:
    """旧指紋の行 → いまの指摘の指紋. 旧指紋が同じ指摘が無い・決められないときは None."""
    if len(candidates) == 1:
        return candidates[0].fingerprint
    same = {c.fingerprint for c in candidates if c.role == rec.get("role") and c.check_id == rec.get("check_id")}
    return same.pop() if len(same) == 1 else None


def load(path: Path, items: list[ReviewItem]) -> Resolved:
    return resolve(load_records(path), items)


def append(path: Path, decisions: dict[str, str], items: list[ReviewItem], *, date: str) -> int:
    """decisions: 指摘の指紋（fp:…）→ adopted / rejected. 書いた件数を返す.

    旧指紋（0.2.0 までのダッシュボードが書いた triage.json）と旧形式（ファイル名#No）も受け付ける
    （旧指紋が同じ指摘が複数あるときは、どれか決められないので誤り）。
    """
    by_id: dict[str, ReviewItem | None] = {}
    for i in items:
        by_id[i.fingerprint_v1] = None if i.fingerprint_v1 in by_id else i
    by_id.update({i.fingerprint: i for i in items})
    by_id.update({i.key: i for i in items})
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = []
    for key, decision in sorted(decisions.items()):
        if decision not in DECISIONS:
            raise ValueError(f"{key}: 判定は adopted / rejected です（{decision}）")
        if key in by_id and by_id[key] is None:
            raise KeyError(f"{key}: 同じ旧指紋の指摘が複数あります。ダッシュボードを作り直して判定し直してください")
        it = by_id.get(key)
        if it is None:
            raise KeyError(f"{key}: reviews/ に該当する指摘がありません")
        recs.append(
            {
                "key": it.key,  # 記録した時点の ファイル名#No（quality-analytics が読む）
                "fingerprint": it.fingerprint_v1,  # 旧指紋（quality-analytics などの読み手が引く値）
                "fingerprint_v2": it.fingerprint,  # ロール・観点を含めた指紋（harness はこれで結び付ける）
                "decision": decision,
                "check_id": it.check_id,
                "severity": it.severity,
                "role": it.role,
                "doc_type": it.doc_type,
                "location": it.location,
                "finding": it.finding,
                "triaged_at": date,
            }
        )
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for rec in recs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(recs)


def stats(history: dict[str, dict]) -> list[Stat]:
    by: dict[str, Stat] = defaultdict(lambda: Stat(""))
    for rec in history.values():
        s = by[rec["check_id"]]
        s.check_id = rec["check_id"]
        if rec["decision"] == "adopted":
            s.adopted += 1
        else:
            s.rejected += 1
    return sorted(by.values(), key=lambda s: s.check_id)


def new_check_candidates(history: dict[str, dict]) -> list[dict]:
    return [
        r
        for r in sorted(history.values(), key=lambda r: str(r.get("key", "")))
        if r["check_id"] == "NEW" and r["decision"] == "adopted" and r.get("severity") in ("MUST", "WANT")
    ]
