# office-review

設計資料（API・バッチ・外部 IF・共通部品・OpenAPI・基本設計の章）を、**種別ごとのチェックリスト** と **5 つのロールの視点** で
Copilot にレビューさせ、人の採択 / 不採択を蓄積して **チェックリストそのものを育てていく** レビューハーネスです。
表の項目間の整合チェック（fieldcheck）も同梱しています。

> 特定の企業・案件の写しではなく、一般的な観点で作り直したものです。実案件の観点は会社の GitHub に複製してから足してください。

## すぐ使う（3 ステップ）

必要なもの: **Python 3.12 以上**（`pyproject.toml` の `requires-python`）と Git。

1. このリポジトリを clone する
2. **Windows**: `setup.cmd` をダブルクリック ／ **macOS・Linux**: `./setup.sh`
   （`harness convert` に使う doc-to-markdown も入ります。要らないときは `setup.cmd --no-convert` / `./setup.sh --no-convert`。
   手で入れるなら `pip install -e ".[convert]"`）
3. VS Code でフォルダを開き、Copilot Chat で `/review inputs/api/order-api.md`（同梱のサンプル）

## 流れ

| 手順 | やること | コマンド / 操作 |
|---|---|---|
| 1 準備 | Office の原本を Markdown にして `inputs/<種別>/` に置く（変換レポートは `data/convert-reports/<種別>/<元ファイル名>.json`） | `harness convert 設計書.xlsx --type api`（doc-to-markdown を使う） |
| 2 事前チェック | 機械で分かる不足（Front Matter の id・version（版数）・status、必須の見出し、表の列）を先に潰す | `harness precheck inputs/api/x.md --type api` |
| 3 AI レビュー | 5 ロールの順にチェックリストで確かめ、結果を表で出す | Copilot Chat で `/review inputs/api/x.md` |
| 4 形式チェック | 結果（`reviews/*_result.md`）の形式を確かめる | `harness lint` |
| 5 トリアージ | ダッシュボードで指摘ごとに採択 / 不採択を選び「トリアージ結果を保存」。`triage.json` は**ブラウザのダウンロードフォルダ**（Windows は `%USERPROFILE%\Downloads`、macOS は `~/Downloads` など）に保存される | `harness dashboard` → `reviews/dashboard.html` を開く |
| 6 蓄積 | 判定を記録し、観点ごとの採択率と追加候補を出す | `harness triage ~/Downloads/triage.json`（保存したパス）→ `harness accumulate` |
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
fieldcheck validate fieldcheck/samples/expense-list.md -r fieldcheck/samples/rules.yaml --table 申請一覧
fieldcheck schema > fieldcheck/rules.schema.json      # ルールの JSON Schema（エディタ補完用）
```

サンプルはわざと誤りを含むので、1 行目は指摘を出して終了コード 1 になります。
ルールの書き方はサンプル（[fieldcheck/samples/](fieldcheck/samples/)）と JSON Schema（[fieldcheck/rules.schema.json](fieldcheck/rules.schema.json)）、
出力形式と終了コードは [fieldcheck/OUTPUT_SPEC.md](fieldcheck/OUTPUT_SPEC.md)。

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
- **CSV / TSV**: Markdown の表と同じく、セル数が列と合わない行は警告（`input.row_shape`）を出します
- **数値**: 桁の位置が ±9999 を超える値（`1E+999999999` など）は数値として扱いません（`fields` で number と宣言していれば
  `schema.type`）。JSON の 4300 桁を超える整数も読めます

## 終了コード

| コード | harness | fieldcheck |
|---|---|---|
| 0 | 問題なし（`precheck`・`lint` は warning・info の指摘だけなら 0） | `--fail-on`（既定 error）以上の指摘が無い |
| 1 | `precheck`・`lint` に error の指摘がある（precheck は MUST の観点の不足）、`checklists --check` で古いチェックリストがある | `--fail-on` 以上の指摘がある |
| 2 | 引数・設定（`config/checks.yaml`）・入力の誤り（ファイルが無い、文字コードを判別できない、`lint --fail-if-empty` で 0 件、`convert` の入力・外部ソフトの問題、`convert` で別の元ファイルから作った同名の Markdown がある） | 引数・ルール・入力の誤り（詳細は [OUTPUT_SPEC.md](fieldcheck/OUTPUT_SPEC.md#終了コード)） |
| 3 | 想定外のエラー（`convert` の変換の失敗を含む） | 想定外のエラー |

## 開発

```bash
./setup.sh && source .venv/bin/activate
ruff check . && ruff format --check . && pytest
```

- 依存の版は `constraints.txt` でそろえます（`pip install -c constraints.txt -e ".[dev,convert]"`。GitHub Actions の deps-lock が作る）
- `[convert]` の doc-to-markdown は、本体と同じ devtools-common の版を固定しているコミットにします（違うと `pip install ".[convert]"` が
  失敗します）。`tests/test_pins.py` と CI の convert ジョブで確かめます
- CI: Linux と Windows のテスト、`.[convert]` を入れてサンプルを `harness convert`、bandit・pip-audit
- カバレッジ: CI（Linux）は `coverage run -m pytest` → `coverage report` で下限（`pyproject.toml` の
  `[tool.coverage.report] fail_under`）を確かめます。手元では `pip install "coverage==7.10.6"` のうえで同じコマンド

## 変更点（次の版）

| 変更 | 以前 | これから |
|---|---|---|
| fieldcheck の数値の `NaN`・`Infinity`（`inf` 等） | 比較で落ちる（終了コード 3） | 数値でない値として扱う（`fields` で number と宣言していれば `schema.type`）。ルールの `min`・`max`・`value` に `.nan`・`.inf` はルールの誤り（終了コード 2） |
| fieldcheck の `--max-findings 0`・負の数 | 指摘 0 件・終了コード 0（検証していないのに成功に見える） | 引数の誤り（終了コード 2）。1 以上を指定し、打ち切らないときは指定しない |
| fieldcheck の YAML 入力の引用符の無い値 | YAML の型で読む（`010` が 8、`no` が False、`2026-04-01` が日付） | 文字列のまま読む（CSV・Markdown の表と同じ。数値・日付の解釈は `fields` の型宣言か自動判定）。`null`・`~`・空は空欄 |
| fieldcheck の JSON Lines のオブジェクトでない行（`[1, 2]` など） | 終了コード 3 | 入力の誤り（終了コード 2、行番号つき） |
| fieldcheck の expression の巨大な繰り返し（`"x" * 100000000000`） | MemoryError（終了コード 3） | 100 万文字（要素）を超える繰り返し・連結、文字列の `%` 書式は「式を評価できません」の指摘 |
| `harness lint` の存在しないパス | 終了コード 3 | 使い方の誤り（終了コード 2） |
| fieldcheck の `--max-findings N` で上限を超えたとき | 先に出た N 件を残す（warning が error を押し出して終了コード 0 になりうる）。集計系ルールの `finish()` の分を切り捨てても `truncated` が出ない | 重い指摘（error → warning → info）から N 件を残す。上限に達した時点で `--fail-on` 以上の指摘が無ければ、見つかるまで（または最後まで）評価を続ける（その間は `--fail-on` 以上の指摘だけを集める）。`finish()` の分で超えたときも `truncated` |
| fieldcheck の expression の `matches(項目, "不正な正規表現")` | `check-rules` を通り、`validate` で終了コード 3 | 式の誤り（`check-rules`・`validate` とも終了コード 2）。正規表現を項目の値から取るときに不正なら「式を評価できません」の指摘 |
| `harness lint` の対象 0 件 | 黙って終了コード 0 | 警告を出す（終了コードは 0 のまま）。`--fail-if-empty` を付けると終了コード 2（CI は付けて実行） |
| fieldcheck の桁の大きすぎる数（`1E+999999999`） | 合計・比較で decimal.Overflow（終了コード 3）。表示に 100 万桁近く並ぶことがある | 数値でない値として扱う（`schema.type`）。表示は ±30 桁を超えると指数表記（`1E+40`） |
| fieldcheck の JSON・JSON Lines の 4300 桁を超える整数 | 終了コード 3 | そのまま数値として読む |
| fieldcheck の CSV・TSV の列より少ない行 | 黙って空として読む | 空として読み、警告 `input.row_shape`（Markdown の表と同じ） |
| `harness precheck` の Shift_JIS の Markdown | 終了コード 3 | UTF-8 → Shift_JIS の順に自動（fieldcheck と同じ）。読めなければ終了コード 2 |
| `harness precheck` の ALL-001（文書 ID・版・承認状態） | Front Matter の `id`・`title` を確かめていた（観点の文の「版数」「status」と食い違い） | 観点の文どおり `id`・`version`（または `版数`）・`status` を確かめる。`title` は見ない。doc2md の Front Matter には version・status が無いので書き足す |
| `harness precheck` の API-001（API ID・メソッド・パス・認証） | 見出しに「概要」「一覧」を含めば通っていた（ほぼ何でも通る） | 列 ID・メソッド・パス・認証 を持つ表があるかを確かめる（API-002 と同じ表の列のチェック） |
| `harness convert` の変換レポートの名前 | `data/convert-reports/<種別>/<拡張子を除いた名前>.json`（`spec.docx` と `spec.xlsx` で上書き） | `data/convert-reports/<種別>/<元ファイル名>.json`（`spec.docx.json`・`spec.xlsx.json`） |
| `harness convert` で、別の元ファイルから作った同名の Markdown があるとき | 警告を出して上書き | 上書きせずに終了コード 2（元ファイルの名前を変えるか、既存の Markdown を移動・削除してから変換） |
| `harness dashboard` の triage.json の保存先 | 案内なし | ダッシュボードとコマンドの出力で、ブラウザのダウンロードフォルダに保存されることと次のコマンドを案内 |
| config/checks.yaml の `machine` の誤り（未知の kind、`pattern` などの必須のキーの欠落） | 未知の kind は黙って飛ばす、必須のキーが無いと precheck が終了コード 3 | 読み込み時に設定の誤り（終了コード 2。どのコマンドでも） |

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
