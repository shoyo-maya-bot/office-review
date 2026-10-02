"""review_harness — 設計資料のレビューを、チェックリストと指摘の蓄積で育てていくための道具."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("office-review")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
