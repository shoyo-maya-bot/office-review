"""トリアージ結果（採択 / 不採択）の蓄積と、観点ごとの採択率の集計.

- data/history.jsonl に 1 指摘 1 行で追記する（同じ key は最新で上書き扱い）
- 採択率 = 採択 / (採択 + 不採択)。件数が少ないうちは判断しない（min_samples）
- 採択率が低い観点は「誤指摘が多い = 観点か無効ケースの書き方が悪い」ので見直し候補にする
- NEW（既存の観点に無い）指摘で採択された MUST / WANT は、観点の追加候補にする
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .results import ReviewItem

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


def load(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                latest[rec["key"]] = rec
    return latest


def append(path: Path, decisions: dict[str, str], items: dict[str, ReviewItem], *, date: str) -> int:
    """decisions: 指摘の key → adopted / rejected. 書いた件数を返す."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for key, decision in sorted(decisions.items()):
            if decision not in DECISIONS:
                raise ValueError(f"{key}: 判定は adopted / rejected です（{decision}）")
            it = items.get(key)
            if it is None:
                raise KeyError(f"{key}: reviews/ に該当する指摘がありません")
            rec = {
                "key": key,
                "decision": decision,
                "check_id": it.check_id,
                "severity": it.severity,
                "role": it.role,
                "doc_type": it.doc_type,
                "finding": it.finding,
                "triaged_at": date,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


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
