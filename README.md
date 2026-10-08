# office-review

設計資料（API・バッチ・外部 IF・共通部品・OpenAPI・基本設計の章）を、**種別ごとのチェックリスト** と **5 つのロールの視点** で
Copilot にレビューさせ、人の採択 / 不採択を蓄積して **チェックリストそのものを育てていく** レビューハーネスです。
表の項目間の整合チェック（fieldcheck）も同梱しています。

> 特定の企業・案件の写しではなく、一般的な観点で作り直したものです。実案件の観点は会社の GitHub に複製してから足してください。

## すぐ使う（3 ステップ）

1. このリポジトリを clone する
2. **Windows**: `setup.cmd` をダブルクリック ／ **macOS・Linux**: `./setup.sh`
   （`harness convert` に使う doc-to-markdown も入ります。要らないときは `setup.cmd --no-convert` / `./setup.sh --no-convert`。
   手で入れるなら `pip install -e ".[convert]"`）
3. VS Code でフォルダを開き、Copilot Chat で `/review inputs/api/order-api.md`（同梱のサンプル）

## 流れ

| 手順 | やること | コマンド / 操作 |
|---|---|---|
| 1 準備 | Office の原本を Markdown にして `inputs/<種別>/` に置く | `harness convert 設計書.xlsx --type api`（doc-to-markdown を使う） |
| 2 事前チェック | 機械で分かる不足（Front Matter・必須の見出し・表の列）を先に潰す | `harness precheck inputs/api/x.md --type api` |
| 3 AI レビュー | 5 ロールの順にチェックリストで確かめ、結果を表で出す | Copilot Chat で `/review inputs/api/x.md` |
| 4 形式チェック | 結果（`reviews/*_result.md`）の形式を確かめる | `harness lint` |
| 5 トリアージ | ダッシュボードで指摘ごとに採択 / 不採択を選ぶ | `harness dashboard` → `reviews/dashboard.html` を開く |
| 6 蓄積 | 判定を記録し、観点ごとの採択率と追加候補を出す | `harness triage triage.json` → `harness accumulate` |
| 7 育てる | 誤指摘の多い観点を直し、繰り返し出る新しい指摘を観点にする | Copilot Chat で `/review-improve` → `config/checks.yaml` を PR |

詳細は [docs/WORKFLOW.md](docs/WORKFLOW.md)、仕組みは [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 観点（チェックリスト）

観点の正は [config/checks.yaml](config/checks.yaml)（34 観点）。[docs/review-checklists/](docs/review-checklists/) はそこから生成したものです。

| 種別 | チェックリスト | 主な観点 |
|---|---|---|
| API | [api-review.md](docs/review-checklists/api-review.md) | 入力定義・応答とメッセージ ID・冪等性・認可・性能 |
| バッチ | [batch-review.md](docs/review-checklists/batch-review.md) | 起動・入出力・STEP・エラーと終了コード・再実行・コミット単位 |
| 外部 IF | [external-if-review.md](docs/review-checklists/external-if-review.md) | 役割分担・項目マッピング・障害時・通信と認証 |
| 共通部品 | [common-review.md](docs/review-checklists/common-review.md) | 責務と利用条件・インタフェース |
| OpenAPI | [oas-review.md](docs/review-checklists/oas-review.md) | 設計書との一致（design-diff）・エラー応答・説明と例 |
| 基本設計（アーキテクチャ / DB） | [architecture-review.md](docs/review-checklists/architecture-review.md)、[db-review.md](docs/review-checklists/db-review.md) | 方式の選定理由・可用性・監視、キーとインデックス・データ量・個人情報 |

全種別共通で、文書管理・用語・あいまいな表現・未決事項・上位文書との整合を見ます。

### 指摘の書き方

| 項目 | 内容 |
|---|---|
| check_id | 観点の ID。チェックリストに無い指摘は `NEW`（追加候補になる） |
| 重要度 | **MUST** 直さないと問題が起きる / **WANT** 直すと良くなる / **NITS** 表記・体裁 / **Q** 質問 |
| ロール | architect / infra / spec_manager / dba / ops |
| 根拠 | 対象のどこを見て、何に照らしたか（必須） |
| 無効となるケースの確認 | 観点ごとの「この観点を当てはめてはいけないケース」に当たらないことの確認（必須。誤指摘を減らす） |

不変条件（根拠のない指摘をしない・推測で断定しない・最終判断は人 など）は [docs/golden-principles.md](docs/golden-principles.md)。

## fieldcheck（表の項目間の整合チェック）

設計書の表やデータの「A なら B は必須」「合計 = 内訳の和」「コードはマスタに存在」などを、YAML のルールで確かめます。
AI レビューの前の機械チェックとして使います。

```bash
fieldcheck validate fieldcheck/samples/expense-list.md -r fieldcheck/samples/rules.yaml --table 経費明細
fieldcheck schema > fieldcheck/rules.schema.json      # ルールの JSON Schema（エディタ補完用）
```

ルールの書き方と出力形式は [fieldcheck/OUTPUT_SPEC.md](fieldcheck/OUTPUT_SPEC.md) とサンプル（[fieldcheck/samples/](fieldcheck/samples/)）。

> **カスタムルール（`--plugin` と entry point）は信頼できるものだけ使ってください。** `--plugin my_rules.py` で指定した
> ファイルと、入っているパッケージの entry point（グループ `field_validation.rules`）は、`fieldcheck validate` のたびに
> **Python のコードとしてそのまま実行されます**（サンドボックスはありません。ファイルの読み書きや通信もできます）。
> 自分たちで書いてレビューしたもの、または中身を確かめたものだけを指定・インストールし、受け取ったルールや
> 出どころの分からないパッケージを入れないでください。ルールファイル（YAML）の `expression` は安全な式だけを評価します（任意のコードは実行しません）。

- **文字コード**: 既定は UTF-8（BOM 付き可）を試し、読めなければ Shift_JIS（cp932）を試します。どちらでも読めなければ
  終了コード 2 で `--encoding` を案内します
- **Markdown の表**: doc-to-markdown のエスケープ（`\\` `\|` `\<` など。doc-to-markdown の docs/OUTPUT_SPEC.md）を元の文字に戻し、
  `<br>` を改行として読みます。セル数が見出しと合わない行は警告（`input.row_shape`）を出します（足りないセルは空として読み、
  多いセルは読まずに値を警告に出します）

## 開発

```bash
./setup.sh && source .venv/bin/activate
ruff check . && ruff format --check . && pytest
```

- 依存の版は `constraints.txt` でそろえます（`pip install -c constraints.txt -e ".[dev,convert]"`。GitHub Actions の deps-lock が作る）
- `[convert]` の doc-to-markdown は、本体と同じ devtools-common の版を固定しているコミットにします（違うと `pip install ".[convert]"` が
  失敗します）。`tests/test_pins.py` と CI の convert ジョブで確かめます
- CI: Linux と Windows のテスト、`.[convert]` を入れてサンプルを `harness convert`、bandit・pip-audit

## 変更点（次の版）

| 変更 | 以前 | これから |
|---|---|---|
| fieldcheck の数値の `NaN`・`Infinity`（`inf` 等） | 比較で落ちる（終了コード 3） | 数値でない値として扱う（`fields` で number と宣言していれば `schema.type`）。ルールの `min`・`max`・`value` に `.nan`・`.inf` はルールの誤り（終了コード 2） |

## 変更点（0.2.0）

| 変更 | 以前 | これから |
|---|---|---|
| fieldcheck の入力の文字コード（既定） | UTF-8 だけ（Shift_JIS は終了コード 3） | UTF-8 → Shift_JIS の順に自動。読めなければ終了コード 2 |
| Markdown の表の `\\` などのエスケープ | そのまま読んでいた（`C:\\data`） | 元の文字に戻す（`C:\data`） |
| セル数が見出しと合わない行 | 黙って切り詰め | 警告 `input.row_shape`（`--fail-on warning` なら終了コード 1） |
| `harness triage` の triage.json が無い・壊れている | 終了コード 3 | 終了コード 2（分かりやすいメッセージ） |
| data/history.jsonl の壊れた行 | 全コマンドが終了コード 3 | 行番号つきで警告して飛ばす |
| `harness precheck` の BOM 付き Markdown | Front Matter が無いと誤判定 | 正しく読む |

## 関連リポジトリ

- [doc-to-markdown](https://github.com/shoyo-maya-bot/doc-to-markdown): Office の設計書を Markdown に（`harness convert` が使う）
- [design-diff](https://github.com/shoyo-maya-bot/design-diff): 設計書の新旧差分、設計書と OpenAPI の差分（OAS-001 の確認に使う）
- [quality-analytics](https://github.com/shoyo-maya-bot/quality-analytics): 指摘の傾向分析（reviews/ と data/history.jsonl を読み込める）

## セキュリティ・ライセンス

- 脆弱性を見つけたら、公開の Issue ではなく [SECURITY.md](SECURITY.md) の方法で知らせてください
- ライセンスは [MIT](LICENSE) です
