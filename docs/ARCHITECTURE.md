# 仕組み

| 部分 | 置き場 | 役割 |
|---|---|---|
| 観点（SSOT） | `config/checks.yaml` | 観点 ID・種別・ロール・重要度・確かめること・無効となるケース・機械チェック |
| チェックリスト | `docs/review-checklists/*.md` | checks.yaml から生成（人と AI が読む形） |
| 不変条件 | `docs/golden-principles.md` | 全種別で守ること |
| ロール | `docs/roles/*.md` | 5 ロールの視点（architect / infra / spec_manager / dba / ops） |
| 対象 | `inputs/<種別>/` | レビュー対象（Markdown / OAS の YAML・JSON） |
| 判断基準 | `docs/references/` | 社内標準・上位文書・外部仕様・用語集 |
| 結果 | `reviews/*_result.md` | AI レビューの結果（表形式。harness lint で検査） |
| 履歴 | `data/history.jsonl` | 指摘ごとの採択 / 不採択（指摘の指紋で特定。下の「判定の結び付け」） |
| 変換レポート | `data/convert-reports/<種別>/` | harness convert の変換レポート（doc2md の JSON。警告 loss.* 等を残す） |
| 集計 | `docs/adoption.md`、`docs/check-candidates.md` | 採択率と観点の追加候補 |
| 道具 | `src/review_harness/`（harness）、`src/field_validation/`（fieldcheck） | 事前チェック・形式チェック・ダッシュボード・蓄積、表の項目間検証 |

指摘の重要度は MUST / WANT / NITS / Q の 4 段階で、quality-analytics・ai-coding のレビュー観点と共通です。

## 判定の結び付け（指紋）

採択 / 不採択の判定は、指摘の **指紋**（`fp:` + 16 桁）で指摘に結び付けます。
指紋は「対象（Front Matter の target）・箇所・指摘の本文」から作るハッシュで、No やレビュー結果のファイル名には左右されません。
全角・半角の違い、空白の数、英字の大小は無視します。再レビューで指摘の順番が変わっても、判定は同じ指摘に付いたままです。

- `data/history.jsonl` の 1 行: `key`（記録した時点の `ファイル名#No`。quality-analytics が読む）、`fingerprint`、`decision`、
  `check_id`、`severity`、`role`、`doc_type`、`location`、`finding`、`triaged_at`
- 同じ指紋の判定が複数あれば、後に書いたものが有効です
- 箇所か指摘の本文を書き換えると別の指摘として扱います（判定し直してください）
- ダッシュボードが保存する `triage.json` のキーも指紋です（旧形式の `ファイル名#No` も受け付けます）

### 旧形式の履歴（fingerprint の無い行）

以前の `data/history.jsonl` は `ファイル名#No` だけで記録していました。そのまま読めます。

- その No にいまある指摘が同じ指摘（指摘の本文と check_id が一致）のときだけ、その指摘の判定として引き継ぎます
- 違う指摘になっている（再レビューで No がずれた等）ときは **孤立した判定** として、`harness dashboard` と
  `harness accumulate` が一覧を表示します。誤った指摘には結び付けません。必要ならダッシュボードで判定し直してください
- 孤立した判定も、観点ごとの採択率（`docs/adoption.md`）には数えます（判定そのものは有効なため）
- ファイルは書き換えません。判定し直すと、新しい形式の行が追記されます
