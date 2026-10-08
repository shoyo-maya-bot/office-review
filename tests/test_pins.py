"""依存の固定（コミット SHA）の食い違いを防ぐ.

[convert] extra の doc-to-markdown が固定している devtools-common が本体と違うと、
pip install ".[convert]" が ResolutionImpossible で失敗する（harness convert が使えない）。

手で書いた注記ではなく、実際の依存を見る:
- pyproject.toml の dependencies / optional-dependencies（git の依存が SHA で固定されているか）
- constraints.txt（git の依存は pip が受け付けないので除外されているか. deps-lock の生成条件と合っているか）
- 入った環境のメタデータ（doc-to-markdown が要求する devtools-common の SHA、入ったコミット）.
  CI の convert ジョブでは OFFICE_REVIEW_REQUIRE_CONVERT=1 にして、入っていなければ失敗にする
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import unittest
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CONSTRAINTS = ROOT / "constraints.txt"
GIT_DEP = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*@\s*git\+\S+?/([\w-]+)\.git@(\S+?)\s*(?:;|$)")


def _project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def _all_deps() -> list[str]:
    project = _project()
    return [*project["dependencies"], *(d for ds in project["optional-dependencies"].values() for d in ds)]


def _git_pins() -> dict[str, str]:
    """pyproject.toml の git の依存: パッケージ名 → 固定した ref."""
    pins = {}
    for d in _all_deps():
        if "git+" in d:
            m = GIT_DEP.match(d)
            if not m:
                raise AssertionError(f"git の依存を読めません: {d}")
            pins[m.group(1).lower()] = m.group(3)
    return pins


def _installed_commit(dist: str) -> str | None:
    """pip が git から入れたときのコミット（direct_url.json）. editable やローカルからなら None."""
    try:
        text = metadata.distribution(dist).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not text:
        return None
    return json.loads(text).get("vcs_info", {}).get("commit_id")


class PinTest(unittest.TestCase):
    def test_git_dependencies_are_pinned_by_sha(self):
        pins = _git_pins()
        self.assertIn("devtools-common", pins)
        self.assertIn("doc-to-markdown", pins)
        for name, ref in pins.items():
            self.assertRegex(ref, r"^[0-9a-f]{40}$", f"{name} がコミット SHA（40 桁）で固定されていません: {ref}")

    def test_constraints_exclude_git_dependencies(self):
        """constraints.txt に git の依存の版が入っていない（入っていると pip install -c が失敗する）.

        deps-lock は --no-emit-package で除外し、末尾に「excluded」として名前を残す.
        その一覧が pyproject の git の依存と一致する.
        """
        text = CONSTRAINTS.read_text(encoding="utf-8")
        pinned = {
            re.split(r"[=<>!~ ;\[]", ln, maxsplit=1)[0].lower()
            for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        }
        git = set(_git_pins())
        self.assertFalse(pinned & git, f"constraints.txt に git の依存が入っています: {sorted(pinned & git)}")
        excluded = set(re.findall(r"^# ([A-Za-z0-9_.-]+)$", text.split("were excluded from the output:", 1)[-1], re.M))
        self.assertEqual(
            excluded, git, "deps-lock.yml の --no-emit-package と pyproject.toml の git の依存がずれています"
        )

    def test_installed_doc_to_markdown_requires_same_devtools_common(self):
        """doc-to-markdown が入っている環境（CI の convert ジョブ）では、実際の依存が本体と同じ SHA か確かめる."""
        pins = _git_pins()
        try:
            requires = metadata.requires("doc-to-markdown") or []
        except metadata.PackageNotFoundError:
            if os.environ.get("OFFICE_REVIEW_REQUIRE_CONVERT") == "1":
                self.fail('doc-to-markdown が入っていません（pip install ".[convert]" の後に流してください）')
            self.skipTest("doc-to-markdown が入っていない（[convert] なし）")
        shas = {m.group(1) for r in requires if (m := re.search(r"devtools-common\.git@([0-9a-f]{40})", r))}
        if not shas and os.environ.get("OFFICE_REVIEW_REQUIRE_CONVERT") != "1":
            self.skipTest("手元の editable などで doc-to-markdown の依存に SHA の固定が無い")
        self.assertEqual(
            shas,
            {pins["devtools-common"]},
            "[convert] の doc-to-markdown が固定している devtools-common が本体と違います。"
            "doc-to-markdown を本体と同じ devtools-common を固定しているコミットにしてください",
        )
        # 実際に入ったコミットも pyproject.toml の固定どおりか（git から入れた場合だけ分かる）
        for dist in ("doc-to-markdown", "devtools-common"):
            commit = _installed_commit(dist)
            if commit is not None:
                self.assertEqual(commit, pins[dist], f"入っている {dist} が pyproject.toml の固定と違います")


if __name__ == "__main__":
    unittest.main()
