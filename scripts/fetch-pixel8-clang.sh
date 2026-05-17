#!/usr/bin/env bash
# Fetch + verify + extract a ZyCromerZ Clang toolchain matching a Pixel 8
# kernel AOSP branch.
#
# Usage:
#   scripts/fetch-pixel8-clang.sh [BRANCH] [CACHE_DIR]
#
#   BRANCH     android14 | android15 | android16   (default: android15)
#   CACHE_DIR  download/extract root (default: $HOME/.cache/zyc-clang)
#
# On success prints, on a single line each:
#   CLANG_PATH=<extract dir>
#   CC=<extract dir>/bin/clang
#   LD=<extract dir>/bin/ld.lld
#   AR=<extract dir>/bin/llvm-ar
#   NM=<extract dir>/bin/llvm-nm
#   OBJCOPY=<extract dir>/bin/llvm-objcopy
#   OBJDUMP=<extract dir>/bin/llvm-objdump
#   READELF=<extract dir>/bin/llvm-readelf
#   STRIP=<extract dir>/bin/llvm-strip
#   LLVM_VERSION=<llvm major>
#
# Suitable for `eval "$(scripts/fetch-pixel8-clang.sh android15)"` in a kernel
# build script, or piping into $GITHUB_ENV in a workflow.

set -euo pipefail

BRANCH="${1:-android15}"
CACHE_DIR="${2:-${HOME}/.cache/zyc-clang}"

case "$BRANCH" in
  android14|android15|android16) ;;
  *) echo "error: unknown BRANCH '$BRANCH' (expected android14|android15|android16)" >&2; exit 2 ;;
esac

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
prefix="${REPO_DIR}/Pixel-8-${BRANCH}"

# link + clang-major must be present; sha256 is optional (older upstream
# releases don't expose a digest via the API).
for f in "${prefix}-link.txt" "${prefix}-clang.txt"; do
  if [[ ! -s "$f" ]]; then
    echo "error: tracker file missing or empty: $f" >&2
    exit 3
  fi
done

LINK="$(cat "${prefix}-link.txt")"
LLVM_MAJOR="$(cat "${prefix}-clang.txt")"
EXPECTED_SHA=""
if [[ -f "${prefix}-sha256.txt" ]]; then
  EXPECTED_SHA="$(cat "${prefix}-sha256.txt")"
fi

mkdir -p "$CACHE_DIR"
TARBALL_NAME="$(basename "$LINK")"
TARBALL_PATH="$CACHE_DIR/$TARBALL_NAME"
EXTRACT_DIR="$CACHE_DIR/${TARBALL_NAME%.tar.gz}"

verify_sha() {
  if [[ -z "$EXPECTED_SHA" ]]; then
    echo "warning: no sha256 recorded for ${BRANCH}; skipping verification" >&2
    return 0
  fi
  local actual
  actual="$(sha256sum "$1" | awk '{print $1}')"
  if [[ "$actual" != "$EXPECTED_SHA" ]]; then
    echo "error: sha256 mismatch for $1" >&2
    echo "  expected: $EXPECTED_SHA" >&2
    echo "  actual:   $actual"       >&2
    return 1
  fi
}

# Reuse already-extracted toolchain if it looks complete and matches the
# recorded sha256.
if [[ -x "$EXTRACT_DIR/bin/clang" ]] && [[ -f "$EXTRACT_DIR/.zyc-sha256" ]] \
   && [[ "$(cat "$EXTRACT_DIR/.zyc-sha256")" == "$EXPECTED_SHA" ]]; then
  :
else
  if [[ ! -f "$TARBALL_PATH" ]] || ! verify_sha "$TARBALL_PATH" 2>/dev/null; then
    echo ">>> downloading $LINK" >&2
    rm -f "$TARBALL_PATH"
    # `--retry 5` for transient CDN hiccups; resume support via -C -.
    curl --fail --location --retry 5 --retry-all-errors \
         -C - -o "$TARBALL_PATH" "$LINK"
  else
    echo ">>> reusing cached $TARBALL_PATH" >&2
  fi

  verify_sha "$TARBALL_PATH"

  echo ">>> extracting to $EXTRACT_DIR" >&2
  rm -rf "$EXTRACT_DIR"
  mkdir -p "$EXTRACT_DIR"
  # ZyC tarballs unpack their contents at the top level (no wrapping dir),
  # so extract straight into EXTRACT_DIR.
  tar -xzf "$TARBALL_PATH" -C "$EXTRACT_DIR"
  echo "$EXPECTED_SHA" > "$EXTRACT_DIR/.zyc-sha256"
fi

if [[ ! -x "$EXTRACT_DIR/bin/clang" ]]; then
  echo "error: $EXTRACT_DIR/bin/clang not found after extraction" >&2
  exit 4
fi

cat <<EOF
CLANG_PATH=$EXTRACT_DIR
CC=$EXTRACT_DIR/bin/clang
LD=$EXTRACT_DIR/bin/ld.lld
AR=$EXTRACT_DIR/bin/llvm-ar
NM=$EXTRACT_DIR/bin/llvm-nm
OBJCOPY=$EXTRACT_DIR/bin/llvm-objcopy
OBJDUMP=$EXTRACT_DIR/bin/llvm-objdump
READELF=$EXTRACT_DIR/bin/llvm-readelf
STRIP=$EXTRACT_DIR/bin/llvm-strip
LLVM_VERSION=$LLVM_MAJOR
EOF
