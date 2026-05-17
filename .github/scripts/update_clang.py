#!/usr/bin/env python3
"""
Sync tracker files for https://github.com/ZyCromerZ/Clang releases.

Outputs (all stdlib, no deps):

  Clang-{N}-commit.txt        upstream LLVM commit SHA
  Clang-{N}-lastbuild.txt     YYYYMMDD build date
  Clang-{N}-link.txt          .tar.gz download URL
  Clang-{N}-sha256.txt        sha256 digest (lower hex, 64 chars)

  Pixel-8-{branch}-*.txt      same four files, but pointing at the LLVM
                              major appropriate for that AOSP kernel branch
                              (with fallback to a newer major if the preferred
                              one is not currently published upstream).

Branches:
    Clang-10 .. Clang-23, Clang-main
    Pixel-8-android14, Pixel-8-android15, Pixel-8-android16

Tag patterns recognised:
    "16.0.6-20260510-release"        -> stable point release (10..16)
    "23.0.0git-20260130-release"     -> rolling main / 17+ (per-major)
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

# --- branch / tag patterns -------------------------------------------------

# Stable point releases like "16.0.6-20260510-release".
STABLE_TAG = re.compile(r"^(\d+)\.\d+\.\d+-(\d{8})-release$")
# Rolling builds like "23.0.0git-20260130-release".
GIT_TAG = re.compile(r"^(\d+)\.\d+\.\d+git-(\d{8})-release$")


def stable_pattern(major: int) -> re.Pattern[str]:
    return re.compile(rf"^{major}\.\d+\.\d+-(\d{{8}})-release$")


def git_pattern(major: int) -> re.Pattern[str]:
    return re.compile(rf"^{major}\.\d+\.\d+git-(\d{{8}})-release$")


# Numbered branches we publish trackers for.
# 10..16 historically shipped as stable point releases.
# 17+ ship only as rolling git builds, so we accept either shape.
BRANCH_PATTERNS: dict[str, list[re.Pattern[str]]] = {}
for n in range(10, 17):
    BRANCH_PATTERNS[f"Clang-{n}"] = [stable_pattern(n)]
for n in range(17, 24):
    BRANCH_PATTERNS[f"Clang-{n}"] = [stable_pattern(n), git_pattern(n)]
# Generic "main" tracker = whichever rolling git build is newest overall.
BRANCH_PATTERNS["Clang-main"] = [re.compile(r"^\d+\.\d+\.\d+git-(\d{8})-release$")]

# --- Pixel 8 kernel aliases ------------------------------------------------
#
# Google's pinned clangs for Pixel 8 (shusky, Tensor G3) per AOSP kernel branch:
#   android14-gs-shusky-5.15  -> clang-r487747c (LLVM 17)
#   android15-gs-shusky-6.1   -> clang-r522817  (LLVM 18)
#   android16-gs-shusky-6.1   -> clang-r563880c (LLVM 19)
# Newer clang typically builds these kernels fine, so each alias has a
# preference list and falls back to the next entry if the preferred LLVM major
# is not currently published by ZyCromerZ/Clang.
PIXEL8_ALIASES: dict[str, list[int]] = {
    "Pixel-8-android14": [17, 18, 19],
    "Pixel-8-android15": [18, 19, 20],
    "Pixel-8-android16": [19, 20, 21],
}

LLVM_COMMIT_RE = re.compile(
    r"https?://github\.com/llvm/llvm-project[/\s]+([0-9a-f]{7,40})",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^sha256:([0-9a-f]{64})$", re.IGNORECASE)


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
            if e.code == 422:
                raise
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


def fetch_releases(max_pages: int = 5) -> list[dict]:
    """Stream paginated releases newest-first; cap at max_pages."""
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
        if len(batch) < 100:
            break
    return out


# ---------------------------------------------------------------------------
# Release parsing
# ---------------------------------------------------------------------------

def pick_clang_asset(assets: Iterable[dict]) -> Optional[dict]:
    asset_list = list(assets)
    for a in asset_list:
        name = a.get("name", "")
        if name.endswith(".tar.gz") and name.lower().startswith("clang-"):
            return a
    for a in asset_list:
        if a.get("name", "").endswith(".tar.gz"):
            return a
    return None


def extract_llvm_commit(body: Optional[str]) -> Optional[str]:
    if not body:
        return None
    m = LLVM_COMMIT_RE.search(body)
    return m.group(1) if m else None


def extract_sha256(asset: dict) -> Optional[str]:
    digest = asset.get("digest")
    if not digest:
        return None
    m = SHA256_RE.match(digest)
    return m.group(1).lower() if m else None


def read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


def write_text(path: Path, value: str) -> None:
    path.write_text(value.rstrip("\n") + "\n")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def best_match(
    releases: list[dict], patterns: Iterable[re.Pattern[str]]
) -> Optional[tuple[str, dict]]:
    """Return the (build_date, release) for the newest release matching any
    of the given patterns. Skips drafts and prereleases."""
    pats = list(patterns)
    best: Optional[tuple[str, dict]] = None
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name", "")
        for p in pats:
            m = p.match(tag)
            if m:
                build_date = m.group(1) if p.groups == 1 else m.groups()[-1]
                if best is None or build_date > best[0]:
                    best = (build_date, rel)
                break
    return best


def write_tracker(
    prefix: str, build_date: str, commit: str, link: str, sha256: str
) -> bool:
    """Write the four tracker files. Returns True if any file actually changed.

    All four files are always written (creating empty ones if a value is
    unavailable, e.g. older releases where the API omits `digest`). That keeps
    the file set self-describing and lets consumer scripts assume the files
    exist."""
    files = {
        f"{prefix}-commit.txt": commit,
        f"{prefix}-lastbuild.txt": build_date,
        f"{prefix}-link.txt": link,
        f"{prefix}-sha256.txt": sha256,
    }
    changed = False
    for name, value in files.items():
        path = REPO_ROOT / name
        if not path.exists() or read_text(path) != value:
            write_text(path, value)
            changed = True
    return changed


def update_branch(branch: str, releases: list[dict]) -> dict:
    """Resolve and (maybe) write a numbered Clang-N or Clang-main tracker."""
    patterns = BRANCH_PATTERNS[branch]
    pick = best_match(releases, patterns)
    if pick is None:
        print(f"[{branch}] no matching upstream release found")
        return {"branch": branch, "status": "no-match", "changed": False}

    build_date, rel = pick
    tag = rel["tag_name"]
    asset = pick_clang_asset(rel.get("assets", []) or [])
    commit = extract_llvm_commit(rel.get("body"))
    link = asset.get("browser_download_url") if asset else None
    sha256 = extract_sha256(asset) if asset else None
    published = rel.get("published_at", "")

    summary = {
        "branch": branch,
        "tag": tag,
        "build_date": build_date,
        "commit": commit,
        "published": published,
        "previous_build": read_text(REPO_ROOT / f"{branch}-lastbuild.txt"),
        "changed": False,
    }

    if not commit:
        summary["status"] = "no-commit"
        print(f"[{branch}] {tag}: could not parse llvm commit; skipping")
        return summary
    if not link:
        summary["status"] = "no-asset"
        print(f"[{branch}] {tag}: no .tar.gz asset; skipping")
        return summary
    if not sha256:
        # API may omit `digest` on very old releases; fall back to empty file.
        sha256 = ""

    current_build = summary["previous_build"]
    if current_build and current_build > build_date:
        summary["status"] = "ahead-of-upstream"
        print(
            f"[{branch}] current build {current_build} is newer than upstream "
            f"{build_date}; skipping"
        )
        return summary

    changed = write_tracker(branch, build_date, commit, link, sha256)
    summary["status"] = "updated" if changed else "up-to-date"
    summary["changed"] = changed
    if changed:
        print(
            f"[{branch}] update -> tag={tag} build={build_date} "
            f"commit={commit[:12]}"
        )
    else:
        print(f"[{branch}] up to date ({tag})")
    return summary


def update_pixel8(
    alias: str, prefs: list[int], releases: list[dict]
) -> dict:
    """Resolve a Pixel 8 alias to the first preferred major that has a release.

    Also writes `Pixel-8-{branch}-clang.txt` containing the chosen LLVM major,
    so kernel CI can know which clang is actually being fetched.
    """
    chosen: Optional[tuple[int, str, dict]] = None  # (major, build_date, rel)
    for major in prefs:
        pats = BRANCH_PATTERNS.get(f"Clang-{major}", [])
        if not pats:
            pats = [stable_pattern(major), git_pattern(major)]
        pick = best_match(releases, pats)
        if pick is not None:
            chosen = (major, pick[0], pick[1])
            break

    if chosen is None:
        print(f"[{alias}] no upstream release for any of {prefs}")
        return {"branch": alias, "status": "no-match", "changed": False}

    major, build_date, rel = chosen
    tag = rel["tag_name"]
    asset = pick_clang_asset(rel.get("assets", []) or [])
    commit = extract_llvm_commit(rel.get("body"))
    link = asset.get("browser_download_url") if asset else None
    sha256 = (extract_sha256(asset) if asset else None) or ""
    summary = {
        "branch": alias,
        "tag": tag,
        "build_date": build_date,
        "commit": commit or "",
        "published": rel.get("published_at", ""),
        "llvm_major": str(major),
        "previous_build": read_text(REPO_ROOT / f"{alias}-lastbuild.txt"),
        "changed": False,
    }

    if not commit or not link:
        summary["status"] = "incomplete"
        print(f"[{alias}] {tag}: missing commit/link; skipping")
        return summary

    changed = write_tracker(alias, build_date, commit, link, sha256)
    # Track which LLVM major resolved.
    major_path = REPO_ROOT / f"{alias}-clang.txt"
    if read_text(major_path) != str(major):
        write_text(major_path, str(major))
        changed = True

    summary["status"] = "updated" if changed else "up-to-date"
    summary["changed"] = changed
    if changed:
        print(
            f"[{alias}] update -> LLVM {major} ({tag}) build={build_date} "
            f"commit={commit[:12]}"
        )
    else:
        print(f"[{alias}] up to date (LLVM {major}, {tag})")
    return summary


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_summary(rows: list[dict]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## ZyCromerZ/Clang tracker sync",
        "",
        "| Branch | Status | LLVM | Tag | Build | Previous | Published |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            "| {b} | {s} | {l} | {t} | {bd} | {pb} | {pd} |".format(
                b=r.get("branch", ""),
                s=r.get("status", ""),
                l=r.get("llvm_major", "-"),
                t=r.get("tag", "-"),
                bd=r.get("build_date", "-"),
                pb=r.get("previous_build") or "-",
                pd=(r.get("published") or "-")[:10],
            )
        )
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"::group::Fetch releases from {UPSTREAM}")
    releases = fetch_releases()
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
        rows.append(row)
        if row.get("changed"):
            changed_any = True

    for alias, prefs in PIXEL8_ALIASES.items():
        print(f"::group::{alias}")
        try:
            row = update_pixel8(alias, prefs, releases)
        finally:
            print("::endgroup::")
        rows.append(row)
        if row.get("changed"):
            changed_any = True

    write_summary(rows)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"changed={'true' if changed_any else 'false'}\n")
            updated = ",".join(r["branch"] for r in rows if r.get("changed"))
            f.write(f"updated_branches={updated}\n")

    print("Done." if not changed_any else "Done. Files updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
