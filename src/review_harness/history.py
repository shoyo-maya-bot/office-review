"""トリアージ結果（採択 / 不採択）の蓄積と、観点ごとの採択率の集計.

- data/history.jsonl に 1 指摘 1 行で追記する。指摘は「指紋」（対象・箇所・指摘の本文のハッシュ）で特定し、
  同じ指紋は最新で上書き扱い。No（ファイル名#No）は再レビューでずれるので結び付けには使わない
- 旧形式の行（fingerprint なし）は、その No にいまある指摘が同じとき（本文と check_id が一致）だけ引き継ぎ、
  違えば「孤立した判定」として報告する（採択率の集計には数える）
- 採択率 = 採択 / (採択 + 不採択)。件数が少ないうちは判断しない（min_samples）
- 採択率が低い観点は「誤指摘が多い = 観点か無効ケースの書き方が悪い」ので見直し候補にする
- NEW（既存の観点に無い）指摘で採択された MUST / WANT は、観点の追加候補にする
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .results import ReviewItem, normalize

DECISIONS = {"adopted", "rejected"}


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


def load_records(path: Path) -> list[dict]:
    """data/history.jsonl の全行（書いた順）."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass
class Resolved:
    """履歴を、いまの指摘に結び付けた結果."""

    decisions: dict[str, dict] = field(default_factory=dict)  # 指紋 → 最新の判定
    orphans: list[dict] = field(default_factory=list)  # どの指摘にも結び付けられない旧形式の判定

    def all_records(self) -> dict[str, dict]:
        """集計用: 指紋ごとの最新の判定 + 孤立した旧形式の判定（旧 key ごとに最新）."""
        out = dict(self.decisions)
        out.update({"legacy:" + r["key"]: r for r in self.orphans})
        return out


def _legacy_matches(rec: dict, item: ReviewItem) -> bool:
    """旧形式（key = ファイル名#No）の判定が、いまその No にある指摘と同じものか.

    旧形式は箇所を記録していないので、記録してある指摘の本文と check_id が一致するときだけ同じとみなす。
    """
    return normalize(str(rec.get("finding", ""))) == normalize(item.finding) and rec.get("check_id") == item.check_id


def resolve(records: list[dict], items: list[ReviewItem]) -> Resolved:
    """判定を指紋で引けるようにする. 同じ指紋は後に書いたものが勝つ.

    - fingerprint がある行: その指紋の判定
    - 旧形式の行（fingerprint なし）: key の No にいまある指摘が同じ指摘のときだけ、その指紋に引き継ぐ。
      違う指摘になっていれば（再レビューで No がずれた等）孤立した判定として返す
    """
    by_key = {i.key: i for i in items}
    res = Resolved()
    orphans: dict[str, dict] = {}
    for rec in records:
        fp = rec.get("fingerprint")
        if not fp:
            item = by_key.get(rec.get("key", ""))
            if item is None or not _legacy_matches(rec, item):
                orphans[rec.get("key", "")] = rec
                continue
            fp = item.fingerprint
            orphans.pop(rec.get("key", ""), None)
        res.decisions[fp] = {**rec, "fingerprint": fp}
    res.orphans = list(orphans.values())
    return res


def load(path: Path, items: list[ReviewItem]) -> Resolved:
    return resolve(load_records(path), items)


def append(path: Path, decisions: dict[str, str], items: list[ReviewItem], *, date: str) -> int:
    """decisions: 指摘の指紋（fp:…）→ adopted / rejected. 書いた件数を返す.

    旧形式の triage.json（ファイル名#No）も受け付ける（いまの reviews/ でその No にある指摘として記録する）。
    """
    by_id = {i.fingerprint: i for i in items}
    by_id.update({i.key: i for i in items})
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = []
    for key, decision in sorted(decisions.items()):
        if decision not in DECISIONS:
            raise ValueError(f"{key}: 判定は adopted / rejected です（{decision}）")
        it = by_id.get(key)
        if it is None:
            raise KeyError(f"{key}: reviews/ に該当する指摘がありません")
        recs.append(
            {
                "key": it.key,  # 記録した時点の ファイル名#No（quality-analytics が読む）
                "fingerprint": it.fingerprint,
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
        for r in sorted(history.values(), key=lambda r: r["key"])
        if r["check_id"] == "NEW" and r["decision"] == "adopted" and r["severity"] in ("MUST", "WANT")
    ]
