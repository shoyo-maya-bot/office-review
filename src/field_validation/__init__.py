"""field-validation — 構造化データの項目間の相関ルールを評価する検証専用ツール.

変換（Office/PDF → 構造化データ）は責務外。doc-to-markdown の出力（Markdown の表）や CSV / JSON / YAML を入力にとる。
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("field-validation")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
