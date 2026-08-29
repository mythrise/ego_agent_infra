import subprocess
from pathlib import Path

import pytest

from scripts import build_submission


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_included_files_rejects_dirty_tracked_working_tree(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    artifacts = repo / "benchmarks" / "artifacts"
    artifacts.mkdir(parents=True)
    (repo / ".gitignore").write_text(
        "benchmarks/artifacts/latest.*\n",
        encoding="utf-8",
    )
    readme = repo / "README.md"
    readme.write_text("indexed\n", encoding="utf-8")
    canonical = artifacts / "canonical.json"
    canonical.write_text("{}\n", encoding="utf-8")

    _git(repo, "init", "-q")
    _git(repo, "add", ".gitignore", "README.md", "benchmarks/artifacts/canonical.json")
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )

    # A reviewed commit must not silently produce a different archive because a
    # tracked file was edited after review.
    readme.write_text("dirty tracked working tree\n", encoding="utf-8")
    (artifacts / "latest.json").write_text("ignored\n", encoding="utf-8")
    (artifacts / "notes.md").write_text("untracked\n", encoding="utf-8")
    monkeypatch.setattr(build_submission, "ROOT", repo)

    with pytest.raises(RuntimeError, match="refusing to package tracked working-tree changes"):
        set(build_submission.included_files())


def test_included_files_returns_only_clean_tracked_files(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    artifacts = repo / "benchmarks" / "artifacts"
    artifacts.mkdir(parents=True)
    (repo / ".gitignore").write_text("benchmarks/artifacts/latest.*\n", encoding="utf-8")
    readme = repo / "README.md"
    readme.write_text("reviewed\n", encoding="utf-8")
    canonical = artifacts / "canonical.json"
    canonical.write_text("{}\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".gitignore", "README.md", "benchmarks/artifacts/canonical.json")
    _git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    (artifacts / "latest.json").write_text("ignored\n", encoding="utf-8")
    (artifacts / "notes.md").write_text("untracked\n", encoding="utf-8")
    monkeypatch.setattr(build_submission, "ROOT", repo)

    included = set(build_submission.included_files())

    assert readme in included
    assert readme.read_text(encoding="utf-8") == "reviewed\n"
    assert canonical in included
    assert artifacts / "latest.json" not in included
    assert artifacts / "notes.md" not in included
