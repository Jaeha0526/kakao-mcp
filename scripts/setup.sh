#!/usr/bin/env bash
# One-shot setup for kakao-mcp on macOS.
#
#   ./scripts/setup.sh
#
# Checks prerequisites, builds kakaocli/kmsg from pinned commits, installs the
# Python deps, creates ~/.config/kakao-mcp/config.json (sending disabled),
# detects your KakaoTalk user id once and saves it, verifies the DB opens, and
# optionally registers the server with Claude Code. Safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$HOME/.config/kakao-mcp"
CONFIG="$CONFIG_DIR/config.json"
BIN="${KAKAO_MCP_HOME:-$HOME/.local/share/kakao-mcp}/bin"

bold() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- prerequisites
bold "Checking prerequisites"
[ "$(uname -s)" = "Darwin" ] || fail "kakao-mcp only works on macOS."
[ -d /Applications/KakaoTalk.app ] || fail "KakaoTalk.app not found in /Applications. Install it from the App Store and log in."
xcode-select -p >/dev/null 2>&1 && command -v swift >/dev/null \
  || fail "Xcode Command Line Tools are required. Run: xcode-select --install"
command -v brew >/dev/null || fail "Homebrew is required: https://brew.sh"
command -v uv >/dev/null || fail "uv is required: brew install uv  (or https://docs.astral.sh/uv/)"
echo "ok"

if ! brew list sqlcipher >/dev/null 2>&1; then
  bold "Installing sqlcipher (needed to read KakaoTalk's encrypted DB)"
  brew install sqlcipher
fi

# ---------------------------------------------------------------- build + deps
bold "Building kakaocli and kmsg from pinned commits"
"$REPO/scripts/install-deps.sh"

bold "Installing Python dependencies"
(cd "$REPO" && uv sync --quiet)
echo "ok"

# ---------------------------------------------------------------- config
bold "Config: $CONFIG"
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
if [ -e "$CONFIG" ]; then
  echo "exists, keeping it"
else
  (umask 077 && cp "$REPO/config.example.json" "$CONFIG")
  # Start with sending off and no allowed chats; the example alias is a placeholder.
  python3 - "$CONFIG" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["send"]["enabled"] = False
d["send"]["allowed_chats"] = {}
json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
EOF
  echo "created (sending disabled)"
fi

config_user_id() {
  python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get("user_id"); print(v if v else "")' "$CONFIG"
}

# ---------------------------------------------------------------- user id
# kakaocli re-detects the user id on every run, which can take minutes on
# accounts with large ids. Detect it once here and pin it in the config.
if [ -z "$(config_user_id)" ]; then
  bold "Detecting your KakaoTalk user id (uses all CPU cores; can take ~2 minutes)"
  detected="$("$BIN/kakaocli" status 2>/dev/null | sed -nE 's/^User ID:[[:space:]]+([0-9]+)$/\1/p' | head -1 || true)"
  if [ -n "$detected" ]; then
    python3 - "$CONFIG" "$detected" <<'EOF'
import json, sys
p, uid = sys.argv[1], int(sys.argv[2])
d = json.load(open(p))
d["user_id"] = uid
json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
EOF
    echo "found and saved to config"
  else
    echo "Could not detect it automatically."
    echo "If kakaocli can open the DB without it, that's fine. Otherwise see README > user_id."
  fi
fi

# ---------------------------------------------------------------- verify
bold "Verifying the KakaoTalk DB can be read"
if (cd "$REPO" && uv run --quiet python - <<'EOF'
import asyncio, json, sys
from kakao_mcp.config import load_config
from kakao_mcp.server import create_server
r = asyncio.run(create_server(load_config()).call_tool("kakao_list_chats", {"limit": 1}))
sys.exit(0 if not r.is_error and json.loads(r.content[0].text)["chats"] else 1)
EOF
); then
  echo "ok: chats are readable"
else
  cat <<EOF
Could not read the DB yet. Usually this is a missing permission:
  System Settings > Privacy & Security > Full Disk Access
  -> enable the app that runs this (Terminal / iTerm / Claude), then re-run this script.
EOF
fi

# ---------------------------------------------------------------- register
bold "Registering with MCP clients"
UV="$(command -v uv)"
if command -v claude >/dev/null; then
  if claude mcp get kakao >/dev/null 2>&1; then
    echo "Claude Code: 'kakao' already registered"
  else
    read -r -p "Register with Claude Code for your user (claude mcp add kakao -s user)? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
      claude mcp add kakao -s user -- "$UV" --directory "$REPO" run kakao-mcp
    fi
  fi
fi

cat <<EOF

Claude desktop app (Chat): quit Claude completely (Cmd+Q) first — it rewrites
its config on exit — then add this under "mcpServers" in
~/Library/Application Support/Claude/claude_desktop_config.json and reopen it:

  "kakao": {
    "command": "$UV",
    "args": ["--directory", "$REPO", "run", "kakao-mcp"]
  }

Permissions (System Settings > Privacy & Security), for the app that runs the server:
  Full Disk Access  - required (reading)
  Accessibility     - only if you enable sending

Sending is off by default. To enable it, edit $CONFIG:
  "send": {"enabled": true, "allowed_chats": {"me": "<exact chat name>"}}

Done.
EOF
