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
| 履歴 | `data/history.jsonl` | 指摘ごとの採択 / 不採択 |
| 集計 | `docs/adoption.md`、`docs/check-candidates.md` | 採択率と観点の追加候補 |
| 道具 | `src/review_harness/`（harness）、`src/field_validation/`（fieldcheck） | 事前チェック・形式チェック・ダッシュボード・蓄積、表の項目間検証 |

指摘の重要度は MUST / WANT / NITS / Q の 4 段階で、quality-analytics・ai-coding のレビュー観点と共通です。
