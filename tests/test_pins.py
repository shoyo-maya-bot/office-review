"""依存の固定（コミット SHA）の食い違いを防ぐ.

[convert] extra の doc-to-markdown が固定している devtools-common が本体と違うと、
pip install ".[convert]" が ResolutionImpossible で失敗する（harness convert が使えない）。
"""

from __future__ import annotations

import re
import tomllib
import unittest
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _sha(spec: str, repo: str) -> str:
    m = re.search(rf"{re.escape(repo)}\.git@([0-9a-f]{{40}})", spec)
    if not m:
        raise AssertionError(f"{repo} がコミット SHA（40 桁）で固定されていません: {spec}")
    return m.group(1)


def _project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def _common_sha() -> str:
    (dep,) = [d for d in _project()["dependencies"] if d.startswith("devtools-common")]
    return _sha(dep, "devtools-common")


class PinTest(unittest.TestCase):
    def test_git_dependencies_are_pinned_by_sha(self):
        project = _project()
        deps = [*project["dependencies"], *(d for ds in project["optional-dependencies"].values() for d in ds)]
        for d in deps:
            if "git+" in d:
                _sha(d, re.search(r"/([\w-]+)\.git@", d).group(1))

    def test_convert_extra_uses_same_devtools_common(self):
        """pyproject.toml に書いた「doc-to-markdown が固定している devtools-common」が本体と同じ SHA."""
        (convert,) = _project()["optional-dependencies"]["convert"]
        _sha(convert, "doc-to-markdown")
        m = re.search(
            r"^# doc-to-markdown が固定している devtools-common: ([0-9a-f]{40})$", PYPROJECT.read_text("utf-8"), re.M
        )
        self.assertIsNotNone(
            m, "pyproject.toml に「# doc-to-markdown が固定している devtools-common: <SHA>」がありません"
        )
        self.assertEqual(
            m.group(1),
            _common_sha(),
            "[convert] の doc-to-markdown を、本体と同じ devtools-common の版に上げてください",
        )

    def test_installed_doc_to_markdown_requires_same_devtools_common(self):
        """doc-to-markdown が入っている環境（CI の convert ジョブ）では、実際の依存も同じ SHA か確かめる."""
        try:
            requires = metadata.requires("doc-to-markdown") or []
        except metadata.PackageNotFoundError:
            return  # 入っていない環境（[convert] なし）では静的な確認（上のテスト）だけ
        shas = [m.group(1) for r in requires if (m := re.search(r"devtools-common\.git@([0-9a-f]{40})", r))]
        if shas:  # 手元の古い editable などで SHA の固定が無いときは比べられない（CI の convert ジョブでは必ずある）
            self.assertEqual(shas[0], _common_sha())


if __name__ == "__main__":
    unittest.main()
