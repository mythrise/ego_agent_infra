"""Fail-closed setuptools hook for the public Worker distribution."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from setuptools import setup
from setuptools.command.build_py import build_py


_PRIVATE_TOKENS = ("evaluator", "sealed", "hidden")


def _is_private_component(component: str) -> bool:
    stem = component.rsplit(".", 1)[0].lower()
    return any(
        stem == token
        or stem.startswith(token + "_")
        or stem.endswith("_" + token)
        for token in _PRIVATE_TOKENS
    )


def is_prohibited_worker_path(path: str) -> bool:
    normalized = tuple(PurePosixPath(path.replace("\\", "/")).parts)
    return any(_is_private_component(component) for component in normalized)


class PublicWorkerBuildPy(build_py):
    """Exclude private-role modules and data even if package discovery broadens."""

    def find_package_modules(self, package, package_dir):  # type: ignore[no-untyped-def]
        modules = super().find_package_modules(package, package_dir)
        return [
            item
            for item in modules
            if not is_prohibited_worker_path(
                "/".join((*package.split("."), item[1] + ".py"))
            )
        ]

    def run(self):  # type: ignore[no-untyped-def]
        super().run()
        build_root = Path(self.build_lib)
        for candidate in sorted(Path(self.build_lib).rglob("*"), reverse=True):
            if candidate.is_file():
                relative = candidate.relative_to(build_root).as_posix()
                if is_prohibited_worker_path(relative):
                    candidate.unlink()
            elif candidate.is_dir() and not any(candidate.iterdir()):
                candidate.rmdir()

    def _get_data_files(self):  # type: ignore[no-untyped-def]
        data_files = super()._get_data_files()
        filtered = []
        for package, src_dir, build_dir, filenames in data_files:
            public_filenames = [
                filename
                for filename in filenames
                if not is_prohibited_worker_path(
                    "/".join((*package.split("."), filename.replace("\\", "/")))
                )
            ]
            filtered.append((package, src_dir, build_dir, public_filenames))
        return filtered


setup(cmdclass={"build_py": PublicWorkerBuildPy})
