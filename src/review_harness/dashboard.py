"""レビュー結果の HTML ダッシュボード（1 ファイルで完結。ネット接続なしで開ける）.

- 重要度・ロール・種別で絞り込み、観点（check_id）と分類ごとの件数を表示
- 指摘ごとに「採択 / 不採択」を選び、triage.json として保存 → harness triage で蓄積
"""

from __future__ import annotations

from collections import Counter
from html import escape

from .checks import Catalog
from .results import ReviewItem

_CSS = """
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--must:#cf222e;--want:#9a6700;--nits:#59636e;--q:#0969da}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#3d444d}}
body{font-family:system-ui,"Segoe UI","Hiragino Sans","Meiryo",sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:16px}
h1{font-size:20px}h2{font-size:16px;margin-top:24px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid var(--line);padding:6px;vertical-align:top;text-align:left}
th{position:sticky;top:0;background:var(--bg)}.MUST{color:var(--must);font-weight:600}.WANT{color:var(--want)}
.NITS{color:var(--nits)}.Q{color:var(--q)}.bar{background:var(--q);height:10px}
.filters{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}.muted{color:var(--muted)}button{padding:6px 12px}
.wrap{overflow-x:auto}
"""

_JS = """
const rows=[...document.querySelectorAll('tbody#items tr')];
function apply(){const v=id=>document.getElementById(id).value;
 rows.forEach(r=>{const ok=['sev','role','type'].every(k=>!v(k)||r.dataset[k]===v(k));r.hidden=!ok;});
 document.getElementById('count').textContent=rows.filter(r=>!r.hidden).length;}
['sev','role','type'].forEach(id=>document.getElementById(id).addEventListener('change',apply));
document.getElementById('save').addEventListener('click',()=>{const out={};
 document.querySelectorAll('input[type=radio]:checked').forEach(i=>{out[i.name]=i.value;});
 const blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='triage.json';a.click();});
apply();
"""


def _options(values: list[str], label: str, el_id: str) -> str:
    opts = "".join(f'<option value="{escape(v)}">{escape(v)}</option>' for v in values)
    return f'<label>{label} <select id="{el_id}"><option value="">すべて</option>{opts}</select></label>'


def render(items: list[ReviewItem], catalog: Catalog, decided: dict[str, str]) -> str:
    checks = catalog.by_id()
    sev_count = Counter(i.severity for i in items)
    cat_count = Counter(checks[i.check_id].category if i.check_id in checks else "（新しい観点）" for i in items)
    top = max(cat_count.values(), default=1)
    cat_rows = "".join(
        f"<tr><td>{escape(c)}</td><td>{n}</td><td><div class='bar' style='width:{n * 100 // top}%'></div></td></tr>"
        for c, n in cat_count.most_common()
    )
    item_rows = []
    for i in items:
        title = checks[i.check_id].title if i.check_id in checks else "新しい観点の候補"
        radios = "".join(
            f'<label><input type="radio" name="{escape(i.key)}" value="{v}"{" checked" if decided.get(i.key) == v else ""}> {t}</label> '
            for v, t in (("adopted", "採択"), ("rejected", "不採択"))
        )
        item_rows.append(
            f'<tr data-sev="{escape(i.severity)}" data-role="{escape(i.role)}" data-type="{escape(i.doc_type)}">'
            f"<td>{escape(i.target)}<br><span class='muted'>{escape(i.file)} #{i.no}</span></td>"
            f"<td class='{escape(i.severity)}'>{escape(i.severity)}</td><td>{escape(i.check_id)}<br>"
            f"<span class='muted'>{escape(title)}</span></td><td>{escape(i.role)}</td><td>{escape(i.location)}</td>"
            f"<td>{escape(i.finding)}</td><td>{escape(i.basis)}</td><td>{radios}</td></tr>"
        )
    summary = " / ".join(f"<span class='{s}'>{s} {sev_count.get(s, 0)}</span>" for s in ("MUST", "WANT", "NITS", "Q"))
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>レビュー結果ダッシュボード</title><style>{_CSS}</style></head><body>
<h1>レビュー結果ダッシュボード</h1>
<p>指摘 {len(items)} 件（{summary}）。採択 / 不採択を選んで「トリアージ結果を保存」→ <code>harness triage triage.json</code></p>
<h2>分類ごとの件数</h2><div class="wrap"><table><thead><tr><th>分類</th><th>件数</th><th></th></tr></thead><tbody>{cat_rows}</tbody></table></div>
<h2>指摘一覧（表示中 <span id="count"></span> 件）</h2>
<div class="filters">{_options(["MUST", "WANT", "NITS", "Q"], "重要度", "sev")}
{_options(sorted(catalog.roles), "ロール", "role")}{_options(sorted({i.doc_type for i in items}), "種別", "type")}
<button id="save">トリアージ結果を保存</button></div>
<div class="wrap"><table><thead><tr><th>対象</th><th>重要度</th><th>観点</th><th>ロール</th><th>箇所</th><th>指摘</th><th>根拠</th><th>判定</th></tr></thead>
<tbody id="items">{"".join(item_rows)}</tbody></table></div>
<script>{_JS}</script>
</body></html>
"""
