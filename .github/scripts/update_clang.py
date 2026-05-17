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

Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

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


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _request(url: str) -> bytes:
    """GET with retry/backoff. Raises on non-recoverable errors."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    last_err: Optional[BaseException] = None
    for attempt in range(1, 5):
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "zyc-clang-tracker")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # 422 = paged past the API hard cap; not transient. Re-raise so the
            # caller can decide to stop pagination cleanly.
            if e.code == 422:
                raise
            # 5xx and 403 (rate-limit) are worth retrying.
            if e.code >= 500 or e.code == 403:
                last_err = e
            else:
                raise
        except urllib.error.URLError as e:
            last_err = e
        sleep_s = 2 ** attempt
        print(
            f"[http] {url} attempt {attempt} failed ({last_err}); "
            f"sleeping {sleep_s}s"
        )
        time.sleep(sleep_s)
    assert last_err is not None
    raise last_err


def fetch_releases(
    needed_branches: set[str], max_pages: int = 5
) -> list[dict]:
    """Stream paginated releases, stopping once every needed branch has at
    least one matching tag (newest-first ordering means later pages can only
    have older releases)."""
    seen: set[str] = set()
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        url = (
            f"https://api.github.com/repos/{UPSTREAM}/releases"
            f"?per_page=100&page={page}"
        )
        try:
            payload = _request(url)
        except urllib.error.HTTPError as e:
            if e.code == 422:
                print(f"[fetch] stopping at page {page} (422 from API)")
                break
            raise
        batch = json.loads(payload.decode())
        if not batch:
            break
        out.extend(batch)
        # Track which branches have at least one match in what we've seen so far.
        for rel in batch:
            tag = rel.get("tag_name", "")
            for branch, pat in BRANCH_PATTERNS.items():
                if branch not in seen and pat.match(tag):
                    seen.add(branch)
        if needed_branches.issubset(seen):
            print(f"[fetch] all branches resolved by page {page}")
            break
        if len(batch) < 100:
            break
    return out


# ---------------------------------------------------------------------------
# Release parsing
# ---------------------------------------------------------------------------

def pick_tarball(assets: Iterable[dict]) -> Optional[str]:
    for a in assets:
        name = a.get("name", "")
        if name.endswith(".tar.gz") and name.lower().startswith("clang-"):
            return a.get("browser_download_url")
    for a in assets:
        if a.get("name", "").endswith(".tar.gz"):
            return a.get("browser_download_url")
    return None


def extract_llvm_commit(body: Optional[str]) -> Optional[str]:
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
    path.write_text(value.rstrip("\n") + "\n")


# ---------------------------------------------------------------------------
# Per-branch update
# ---------------------------------------------------------------------------

def update_branch(branch: str, releases: list[dict]) -> Optional[dict]:
    """Update tracker files for one branch.

    Returns a summary dict (always, for reporting), with a `changed` boolean.
    """
    pattern = BRANCH_PATTERNS[branch]
    best: Optional[tuple[str, dict]] = None
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
        return {"branch": branch, "status": "no-match", "changed": False}

    build_date, rel = best
    tag = rel["tag_name"]
    commit = extract_llvm_commit(rel.get("body"))
    link = pick_tarball(rel.get("assets", []) or [])
    published = rel.get("published_at", "")

    if not commit:
        print(f"[{branch}] {tag}: could not parse llvm commit; skipping")
        return {
            "branch": branch, "status": "no-commit", "tag": tag,
            "changed": False,
        }
    if not link:
        print(f"[{branch}] {tag}: no .tar.gz asset; skipping")
        return {
            "branch": branch, "status": "no-asset", "tag": tag,
            "changed": False,
        }

    commit_path = REPO_ROOT / f"{branch}-commit.txt"
    build_path = REPO_ROOT / f"{branch}-lastbuild.txt"
    link_path = REPO_ROOT / f"{branch}-link.txt"

    current_build = read_text(build_path)
    current_commit = read_text(commit_path)
    current_link = read_text(link_path)

    summary = {
        "branch": branch,
        "tag": tag,
        "build_date": build_date,
        "commit": commit,
        "published": published,
        "previous_build": current_build,
        "changed": False,
    }

    if (
        current_build == build_date
        and current_commit == commit
        and current_link == link
    ):
        print(f"[{branch}] up to date ({tag})")
        summary["status"] = "up-to-date"
        return summary

    if current_build and current_build > build_date:
        print(
            f"[{branch}] current build {current_build} is newer than upstream "
            f"{build_date}; skipping"
        )
        summary["status"] = "ahead-of-upstream"
        return summary

    print(
        f"[{branch}] update -> tag={tag} build={build_date} "
        f"commit={commit[:12]}"
    )
    write_text(commit_path, commit)
    write_text(build_path, build_date)
    write_text(link_path, link)
    summary["status"] = "updated"
    summary["changed"] = True
    return summary


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_summary(rows: list[dict]) -> None:
    """Render a Markdown table to $GITHUB_STEP_SUMMARY when present."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## ZyCromerZ/Clang tracker sync",
        "",
        "| Branch | Status | Tag | Build | Previous | Published |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        if r is None:
            continue
        lines.append(
            "| {branch} | {status} | {tag} | {build} | {prev} | {pub} |".format(
                branch=r.get("branch", ""),
                status=r.get("status", ""),
                tag=r.get("tag", "-"),
                build=r.get("build_date", "-"),
                prev=r.get("previous_build", "-") or "-",
                pub=(r.get("published") or "-")[:10],
            )
        )
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"::group::Fetch releases from {UPSTREAM}")
    releases = fetch_releases(needed_branches=set(BRANCH_PATTERNS))
    print(f"Fetched {len(releases)} releases")
    print("::endgroup::")

    rows: list[dict] = []
    changed_any = False
    for branch in BRANCH_PATTERNS:
        print(f"::group::{branch}")
        try:
            row = update_branch(branch, releases)
        finally:
            print("::endgroup::")
        if row:
            rows.append(row)
            if row.get("changed"):
                changed_any = True

    write_summary(rows)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"changed={'true' if changed_any else 'false'}\n")
            updated = ",".join(
                r["branch"] for r in rows if r.get("changed")
            )
            f.write(f"updated_branches={updated}\n")

    print("Done." if not changed_any else "Done. Files updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
