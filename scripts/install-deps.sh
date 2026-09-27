#!/usr/bin/env bash
# Build kakaocli and kmsg from source at pinned, reviewed commits.
#
# Binaries are installed to ~/.local/share/kakao-mcp/bin, which kakao-mcp
# checks before PATH. Bump a pin only after reviewing the diff between commits.
set -euo pipefail

# Fork of silver-flight-group/kakaocli v0.6.0 (8b6ffcf, reviewed 2026-09-26) plus
# the `history` command (cursor pagination + attachments) that kakao-mcp needs
# and upstream PR #26 (parallel user id detection).
KAKAOCLI_REPO="https://github.com/Jaeha0526/kakaocli.git"
KAKAOCLI_COMMIT="c0fce229f5428bb16cfb7ff7052626368fa8cc2c"  # branch kakao-mcp

KMSG_REPO="https://github.com/channprj/kmsg.git"
KMSG_COMMIT="54cbff790b409214735ef365a621d791034fe500"      # v1.260921.0, reviewed 2026-09-26

ROOT="${KAKAO_MCP_HOME:-$HOME/.local/share/kakao-mcp}"
SRC="$ROOT/src"
BIN="$ROOT/bin"

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: '$1' is required" >&2; exit 1; }; }
need git
need swift

if ! pkg-config --exists sqlcipher 2>/dev/null && ! brew list sqlcipher >/dev/null 2>&1; then
  echo "error: sqlcipher is required for kakaocli. Install it with: brew install sqlcipher" >&2
  exit 1
fi

checkout() {  # repo commit dir
  local repo=$1 commit=$2 dir=$3
  if [ ! -d "$dir/.git" ]; then
    git clone --quiet "$repo" "$dir"
  fi
  git -C "$dir" remote set-url origin "$repo"
  git -C "$dir" fetch --quiet origin
  git -C "$dir" -c advice.detachedHead=false checkout --quiet "$commit"
  local head
  head=$(git -C "$dir" rev-parse HEAD)
  if [ "$head" != "$commit" ]; then
    echo "error: $dir is at $head, expected $commit" >&2
    exit 1
  fi
  if [ -n "$(git -C "$dir" status --porcelain)" ]; then
    echo "error: $dir has local modifications; remove it and rerun" >&2
    exit 1
  fi
}

mkdir -p "$SRC" "$BIN"

echo "==> kakaocli @ ${KAKAOCLI_COMMIT:0:7}"
checkout "$KAKAOCLI_REPO" "$KAKAOCLI_COMMIT" "$SRC/kakaocli"
(cd "$SRC/kakaocli" && swift build -c release --disable-sandbox)
install -m 0755 "$SRC/kakaocli/.build/release/kakaocli" "$BIN/kakaocli"

echo "==> kmsg @ ${KMSG_COMMIT:0:7}"
checkout "$KMSG_REPO" "$KMSG_COMMIT" "$SRC/kmsg"
(cd "$SRC/kmsg" && swift build -c release --disable-sandbox)
install -m 0755 "$SRC/kmsg/.build/release/kmsg" "$BIN/kmsg"

echo
echo "Installed:"
echo "  $BIN/kakaocli"
echo "  $BIN/kmsg"
echo
echo "Next: grant permissions in System Settings > Privacy & Security"
echo "  Full Disk Access : the app that runs the MCP server (e.g. Claude) — needed to read the DB"
echo "  Accessibility    : the same app — needed only for sending"
