#!/usr/bin/env python3
"""
Update the Clang-*-{commit,lastbuild,link}.txt tracker files in this repo
based on the latest releases from https://github.com/ZyCromerZ/Clang.

Branches tracked:
    Clang-10 .. Clang-16  -> tags like "16.0.6-20260510-release"
    Clang-main            -> tags like "23.0.0git-20260130-release"

For each branch we look for the most recent release whose tag matches the
branch's pattern and, if its build date is newer than what is currently
recorded, rewrite the three tracker files.

Outputs are written using stdlib only (no external deps).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

UPSTREAM = "ZyCromerZ/Clang"
REPO_ROOT = Path(__file__).resolve().parents[2]

# Branch -> regex matching the upstream tag for that branch.
BRANCH_PATTERNS: dict[str, re.Pattern[str]] = {
    **{
        f"Clang-{n}": re.compile(rf"^{n}\.\d+\.\d+-(\d{{8}})-release$")
        for n in range(10, 17)
    },
    "Clang-main": re.compile(r"^\d+\.\d+\.\d+git-(\d{8})-release$"),
}

LLVM_COMMIT_RE = re.compile(
    r"https?://github\.com/llvm/llvm-project[/\s]+([0-9a-f]{7,40})",
    re.IGNORECASE,
)


def fetch_releases(max_pages: int = 5) -> list[dict]:
    """Fetch releases from the upstream repo (paginated, newest first).

    Releases come back newest-first; the first page (100) is normally enough
    to find the latest match for every branch pattern. We cap pagination and
    tolerate 422 responses (GitHub returns 422 once you page past its hard
    limit on releases).
    """
    releases: list[dict] = []
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    for page in range(1, max_pages + 1):
        url = (
            f"https://api.github.com/repos/{UPSTREAM}/releases"
            f"?per_page=100&page={page}"
        )
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "zyc-clang-tracker")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                batch = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 422:
                print(f"[fetch] stopping at page {page} (422 from API)")
                break
            print(
                f"::error::GitHub API error on page {page}: {e}",
                file=sys.stderr,
            )
            raise
        if not batch:
            break
        releases.extend(batch)
        if len(batch) < 100:
            break
    return releases


def pick_tarball(assets: Iterable[dict]) -> str | None:
    for a in assets:
        name = a.get("name", "")
        if name.endswith(".tar.gz") and name.lower().startswith("clang-"):
            return a.get("browser_download_url")
    # Fallback: any .tar.gz asset.
    for a in assets:
        if a.get("name", "").endswith(".tar.gz"):
            return a.get("browser_download_url")
    return None


def extract_llvm_commit(body: str | None) -> str | None:
    if not body:
        return None
    m = LLVM_COMMIT_RE.search(body)
    return m.group(1) if m else None


def read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


def write_text(path: Path, value: str) -> None:
    # Match existing format: single line with trailing newline.
    path.write_text(value.rstrip("\n") + "\n")


def update_branch(branch: str, releases: list[dict]) -> bool:
    """Update tracker files for one branch. Returns True if anything changed."""
    pattern = BRANCH_PATTERNS[branch]
    best: tuple[str, dict] | None = None  # (build_date, release)
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name", "")
        m = pattern.match(tag)
        if not m:
            continue
        build_date = m.group(1)
        if best is None or build_date > best[0]:
            best = (build_date, rel)

    if best is None:
        print(f"[{branch}] no matching upstream release found")
        return False

    build_date, rel = best
    tag = rel["tag_name"]
    commit = extract_llvm_commit(rel.get("body"))
    link = pick_tarball(rel.get("assets", []) or [])

    if not commit:
        print(f"[{branch}] {tag}: could not parse llvm commit from body, skipping")
        return False
    if not link:
        print(f"[{branch}] {tag}: no .tar.gz asset, skipping")
        return False

    commit_path = REPO_ROOT / f"{branch}-commit.txt"
    build_path = REPO_ROOT / f"{branch}-lastbuild.txt"
    link_path = REPO_ROOT / f"{branch}-link.txt"

    current_build = read_text(build_path)
    current_commit = read_text(commit_path)
    current_link = read_text(link_path)

    if (
        current_build == build_date
        and current_commit == commit
        and current_link == link
    ):
        print(f"[{branch}] up to date ({tag})")
        return False

    # Only move forward in time; never regress to an older build.
    if current_build and current_build > build_date:
        print(
            f"[{branch}] current build {current_build} is newer than upstream "
            f"{build_date}, skipping"
        )
        return False

    print(
        f"[{branch}] update -> tag={tag} build={build_date} "
        f"commit={commit[:12]}"
    )
    write_text(commit_path, commit)
    write_text(build_path, build_date)
    write_text(link_path, link)
    return True


def main() -> int:
    print(f"Fetching releases from {UPSTREAM}...")
    releases = fetch_releases()
    print(f"Fetched {len(releases)} releases")

    changed_any = False
    for branch in BRANCH_PATTERNS:
        if update_branch(branch, releases):
            changed_any = True

    # Expose result for the workflow step.
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"changed={'true' if changed_any else 'false'}\n")

    print("Done." if not changed_any else "Done. Files updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
