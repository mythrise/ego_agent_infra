#!/usr/bin/env python3
"""Download official pinned files and verify their SHA-256 digests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="validate lock shape only")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "official-contract.lock.json").read_text(encoding="utf-8"))
    required = {"schema", "repository", "stable", "main", "apiVersion", "artifacts"}
    if not required.issubset(lock):
        print("contract lock is missing required fields", file=sys.stderr)
        return 2
    if lock["apiVersion"] != "agentteams.io/v1beta1":
        print("unexpected AgentTeams API version", file=sys.stderr)
        return 2
    for name in ("stable", "main"):
        commit = str(lock.get(name, {}).get("commit", ""))
        if not re.fullmatch(r"[a-f0-9]{40}", commit):
            print("invalid %s commit pin" % name, file=sys.stderr)
            return 2
    artifact_paths = set()
    for artifact in lock.get("artifacts", []):
        if set(artifact) != {"path", "sha256"}:
            print("invalid artifact lock entry", file=sys.stderr)
            return 2
        if artifact["path"] in artifact_paths or artifact["path"].startswith("/") or ".." in Path(
            artifact["path"]
        ).parts:
            print("unsafe or duplicate artifact path", file=sys.stderr)
            return 2
        artifact_paths.add(artifact["path"])
        if not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
            print("invalid artifact digest", file=sys.stderr)
            return 2
    if not artifact_paths:
        print("contract lock contains no artifacts", file=sys.stderr)
        return 2
    if args.offline:
        print("lock-shape-ok (offline; upstream bytes not verified)")
        return 0
    commit = lock["main"]["commit"]
    base = "https://raw.githubusercontent.com/agentscope-ai/AgentTeams/%s/" % commit
    for artifact in lock["artifacts"]:
        url = base + artifact["path"]
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                payload = response.read()
        except (OSError, urllib.error.URLError) as error:
            print("failed to fetch %s: %s" % (url, error), file=sys.stderr)
            return 2
        actual = hashlib.sha256(payload).hexdigest()
        if actual != artifact["sha256"]:
            print(
                "contract drift: %s expected=%s actual=%s"
                % (artifact["path"], artifact["sha256"], actual),
                file=sys.stderr,
            )
            return 1
        print("verified %s" % artifact["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
