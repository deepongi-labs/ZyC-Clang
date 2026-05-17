# QUICK INFO
- compiler script : https://github.com/ZyCromerZ/tc-build
- just, thats it xD
# SDClang 16.x.x
- 16.0.2.0: https://gitlab.com/ZyCromerZ/sdclang-16.0.2.0
- 16.1.0.1: https://gitlab.com/ZyCromerZ/sdclang-16.1.0.1

## Tracker files

A scheduled workflow (`.github/workflows/update-clang.yml`) keeps these in sync
with the latest [ZyCromerZ/Clang](https://github.com/ZyCromerZ/Clang) release
for each tracked LLVM major:

| Pattern                   | Contents                                       |
| ------------------------- | ---------------------------------------------- |
| `Clang-{N}-commit.txt`    | upstream LLVM commit SHA                       |
| `Clang-{N}-lastbuild.txt` | build date (`YYYYMMDD`) from the upstream tag  |
| `Clang-{N}-link.txt`      | direct `.tar.gz` download URL                  |
| `Clang-{N}-sha256.txt`    | sha256 digest (empty for older releases where  |
|                           | the GitHub API does not expose `digest`)       |

`N` ranges over `10`..`23` plus `main` (rolling, currently 23.0.0git).

### Pixel 8 kernel aliases

For Pixel 8 (shusky / Tensor G3) kernel builds there are also alias trackers
that resolve to the LLVM major appropriate for each AOSP kernel branch, with
graceful fallback if a preferred LLVM major is not currently published:

| Alias                       | LLVM preference order |
| --------------------------- | --------------------- |
| `Pixel-8-android14-*.txt`   | 17, 18, 19            |
| `Pixel-8-android15-*.txt`   | 18, 19, 20            |
| `Pixel-8-android16-*.txt`   | 19, 20, 21            |

Each alias produces the same four files as the numbered series, plus
`Pixel-8-{branch}-clang.txt` containing the resolved LLVM major.

### Helper

`scripts/fetch-pixel8-clang.sh` downloads, verifies (sha256 when available),
extracts to `~/.cache/zyc-clang`, and prints `KEY=VALUE` lines suitable for
`eval` or `>> $GITHUB_ENV`:

```sh
eval "$(scripts/fetch-pixel8-clang.sh android15)"
make ARCH=arm64 LLVM=1 LLVM_IAS=1 \
     CC="$CC" LD="$LD" AR="$AR" NM="$NM" \
     OBJCOPY="$OBJCOPY" OBJDUMP="$OBJDUMP" \
     READELF="$READELF" STRIP="$STRIP"
```

The cache is keyed on the tarball name plus its sha256, so unchanged builds
are reused across CI runs.
