#!/usr/bin/env python3
"""Build a deterministic, secret-scanned final source and evidence ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from apps.api.research_os.cli import compile_file


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "EgoAgentOS-final-20260902"
FIXED_ZIP_TIME = (2026, 9, 2, 0, 0, 0)
SECRET_PATTERNS = (
    re.compile(rb"\bwk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bghp_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
)
SENSITIVE_NAMES = {".env", "id_rsa", "id_ed25519", "credentials.json"}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _tracked_files() -> List[Path]:
    return [ROOT / value for value in _git("ls-files").splitlines() if value]


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _scan(relative: str, payload: bytes) -> None:
    path = Path(relative)
    if path.name in SENSITIVE_NAMES or path.suffix.lower() in {".pem", ".key", ".p12"}:
        raise ValueError("refusing sensitive filename: %s" % relative)
    for pattern in SECRET_PATTERNS:
        if pattern.search(payload):
            raise ValueError("possible credential in release file: %s" % relative)


def _zip_info(name: str, executable: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo("%s/%s" % (PREFIX, name), FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16
    return info


def _release_files() -> List[Tuple[str, bytes, bool]]:
    files: List[Tuple[str, bytes, bool]] = []
    for path in _tracked_files():
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT).as_posix()
        payload = path.read_bytes()
        _scan(relative, payload)
        executable = bool(path.stat().st_mode & 0o111)
        files.append((relative, payload, executable))

    with tempfile.TemporaryDirectory(prefix="egoagentos-final-") as tmp:
        generated = Path(tmp) / "ego3d-b-compiled"
        compile_file(ROOT / "examples/ego3d_b_branch/input.yaml", generated)
        for path in sorted(generated.glob("*.json")):
            relative = "generated/ego3d-b-compiled/%s" % path.name
            payload = path.read_bytes()
            _scan(relative, payload)
            files.append((relative, payload, False))
    return sorted(files, key=lambda value: value[0])


def build(output: Path) -> Dict[str, object]:
    files = _release_files()
    records = [
        {"path": relative, "bytes": len(payload), "sha256": _sha(payload)}
        for relative, payload, _ in files
    ]
    manifest: Dict[str, object] = {
        "schema_version": "egoagentos-final-release/v1",
        "release": PREFIX,
        "git_commit": _git("rev-parse", "HEAD"),
        "file_count": len(records),
        "truth_boundary": {
            "local_contracts": "LIVE_LOCAL",
            "web_demo": "SYNTHETIC_FIXTURE",
            "tdsql_nexa": "NOT_CONFIGURED",
            "tencentdb_agent_memory": "NOT_CONFIGURED",
            "official_agentteams_matrix_gpu": "NOT_RUN",
        },
        "files": records,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _scan("RELEASE_MANIFEST.json", manifest_payload)

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compresslevel=9) as archive:
        archive.writestr(_zip_info("RELEASE_MANIFEST.json", False), manifest_payload)
        for relative, payload, executable in files:
            archive.writestr(_zip_info(relative, executable), payload)

    with zipfile.ZipFile(output, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC verification failed")
        loaded = json.loads(archive.read("%s/RELEASE_MANIFEST.json" % PREFIX))
        for record in loaded["files"]:
            payload = archive.read("%s/%s" % (PREFIX, record["path"]))
            if _sha(payload) != record["sha256"]:
                raise RuntimeError("release digest mismatch: %s" % record["path"])

    return {
        "path": str(output.resolve()),
        "bytes": output.stat().st_size,
        "sha256": _sha(output.read_bytes()),
        "files": len(files) + 1,
        "verification": "PASS",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / (PREFIX + ".zip"),
    )
    args = parser.parse_args(argv)
    print(json.dumps(build(args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
