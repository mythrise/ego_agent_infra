"""Fail-closed setuptools hooks for the public Worker distribution."""

from __future__ import annotations

import shutil
import runpy
import tarfile
import zipfile
from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist
from setuptools.errors import SetupError

_PROJECT_ROOT = Path(__file__).resolve().parent
_POLICY = runpy.run_path(str(_PROJECT_ROOT / "worker_distribution.py"))
KNOWN_PRIVATE_SOURCE_FILES = _POLICY["KNOWN_PRIVATE_SOURCE_FILES"]
PUBLIC_WHEEL_PAYLOAD = _POLICY["PUBLIC_WHEEL_PAYLOAD"]
PublicArtifactError = _POLICY["PublicArtifactError"]
validate_complete_public_staging = _POLICY["validate_complete_public_staging"]
validate_discovered_public_files = _POLICY["validate_discovered_public_files"]
validate_no_ambiguous_private_sources = _POLICY["validate_no_ambiguous_private_sources"]
validate_public_staging_subset = _POLICY["validate_public_staging_subset"]
validate_public_worker_sdist = _POLICY["validate_public_worker_sdist"]
validate_public_worker_wheel = _POLICY["validate_public_worker_wheel"]
tar_archive_members = _POLICY["tar_archive_members"]
zip_archive_members = _POLICY["zip_archive_members"]


def _fail_closed(error: PublicArtifactError) -> None:
    raise SetupError(str(error)) from error


def _relative_source(filename: str) -> str:
    candidate = Path(filename)
    if not candidate.is_absolute():
        candidate = _PROJECT_ROOT / candidate
    try:
        return candidate.resolve().relative_to(_PROJECT_ROOT).as_posix()
    except ValueError as exc:
        raise SetupError(f"setuptools selected source outside project root: {filename}") from exc


def _staged_files(build_root: Path) -> list[str]:
    if not build_root.exists():
        return []
    return sorted(
        candidate.relative_to(build_root).as_posix()
        for candidate in build_root.rglob("*")
        if candidate.is_file()
    )


def _validate_source_tree() -> None:
    try:
        validate_no_ambiguous_private_sources(_PROJECT_ROOT)
    except PublicArtifactError as exc:
        _fail_closed(exc)


class PublicWorkerBuildPy(build_py):
    """Build the exact public payload from an empty, verified staging tree."""

    def find_modules(self):  # type: ignore[no-untyped-def]
        modules = super().find_modules()
        sources = [_relative_source(item[2]) for item in modules]
        try:
            validate_discovered_public_files(sources)
        except PublicArtifactError as exc:
            _fail_closed(exc)
        return [
            item
            for item, source in zip(modules, sources)
            if source in PUBLIC_WHEEL_PAYLOAD
        ]

    def find_package_modules(self, package, package_dir):  # type: ignore[no-untyped-def]
        modules = super().find_package_modules(package, package_dir)
        sources = [_relative_source(item[2]) for item in modules]
        try:
            validate_discovered_public_files(sources)
        except PublicArtifactError as exc:
            _fail_closed(exc)
        return [
            item
            for item, source in zip(modules, sources)
            if source not in KNOWN_PRIVATE_SOURCE_FILES and source in PUBLIC_WHEEL_PAYLOAD
        ]

    def _get_data_files(self):  # type: ignore[no-untyped-def]
        data_files = super()._get_data_files()
        discovered = [
            "/".join((*package.split("."), filename.replace("\\", "/")))
            for package, _src_dir, _build_dir, filenames in data_files
            for filename in filenames
        ]
        try:
            validate_discovered_public_files(discovered)
        except PublicArtifactError as exc:
            _fail_closed(exc)
        return data_files

    def run(self):  # type: ignore[no-untyped-def]
        _validate_source_tree()
        if getattr(self, "editable_mode", False):
            super().run()
            return
        build_root = Path(self.build_lib)
        if build_root.is_symlink():
            raise SetupError("public Worker staging root must not be a symlink")
        try:
            validate_public_staging_subset(_staged_files(build_root))
        except PublicArtifactError as exc:
            _fail_closed(exc)
        if build_root.exists():
            shutil.rmtree(build_root)
        build_root.mkdir(parents=True)

        super().run()

        try:
            validate_complete_public_staging(_staged_files(build_root))
        except PublicArtifactError as exc:
            _fail_closed(exc)


class PublicWorkerBdistWheel(bdist_wheel):
    """Validate the final wheel archive against the production allowlist."""

    def run(self):  # type: ignore[no-untyped-def]
        before = set(Path(self.dist_dir).glob("*.whl"))
        super().run()
        created = set(Path(self.dist_dir).glob("*.whl")) - before
        if len(created) != 1:
            raise SetupError(f"expected one newly built Worker wheel, found {len(created)}")
        wheel_path = next(iter(created))
        with zipfile.ZipFile(wheel_path) as archive:
            try:
                validate_public_worker_wheel(zip_archive_members(archive.infolist()))
            except PublicArtifactError as exc:
                _fail_closed(exc)


class PublicWorkerSdist(sdist):
    """Validate the final source archive against the production allowlist."""

    def run(self):  # type: ignore[no-untyped-def]
        _validate_source_tree()
        super().run()
        if len(self.archive_files) != 1:
            raise SetupError(f"expected one newly built Worker sdist, found {len(self.archive_files)}")
        with tarfile.open(self.archive_files[0], "r:gz") as archive:
            members = tar_archive_members(archive.getmembers())
        try:
            validate_public_worker_sdist(members)
        except PublicArtifactError as exc:
            _fail_closed(exc)


setup(
    cmdclass={
        "bdist_wheel": PublicWorkerBdistWheel,
        "build_py": PublicWorkerBuildPy,
        "sdist": PublicWorkerSdist,
    }
)
