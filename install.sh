#!/usr/bin/env bash
# zai-monitor installer — clones the repo, sets up a venv, installs deps,
# and prepares .env. Re-runnable: safe on an existing checkout.
#
# One-liner:
#   curl -fsSL https://raw.githubusercontent.com/jpalmae/zai-monitor/main/install.sh | bash
#
# Install into a specific dir:
#   curl -fsSL https://raw.githubusercontent.com/jpalmae/zai-monitor/main/install.sh | bash -s -- my/dir
#
# Or run locally:
#   bash install.sh [target_dir]

set -euo pipefail

REPO="https://github.com/jpalmae/zai-monitor.git"
TARGET_DIR="${1:-$HOME/zai-monitor}"

cyan()  { printf "\033[36m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*" >&2; }
die()   { red "error: $*"; exit 1; }

# ---- 1. Check Python ---------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 not found. Install Python 3.10+ from https://python.org"
PY_VER=$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys;print(1 if sys.version_info>=(3,10) else 0)')
[ "$PY_OK" = "1" ] || die "Python 3.10+ required (found $PY_VER)."
cyan ">> Python $PY_VER OK"

# ---- 2. Clone or update ------------------------------------------------------
if [ -d "$TARGET_DIR/.git" ]; then
    cyan ">> Updating existing checkout at $TARGET_DIR"
    git -C "$TARGET_DIR" pull --ff-only
else
    cyan ">> Cloning into $TARGET_DIR"
    mkdir -p "$TARGET_DIR"
    git clone --depth 1 "$REPO" "$TARGET_DIR"
fi
cd "$TARGET_DIR"

# ---- 3. venv + deps ----------------------------------------------------------
cyan ">> Creating virtualenv (.venv)"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
cyan ">> Installing dependencies (this can take a minute)"
pip install --quiet --upgrade pip
pip install --quiet -e ".[tui,telegram,dev]" httpx python-dotenv

# ---- 4. .env -----------------------------------------------------------------
if [ ! -f .env ]; then
    cp .env.example .env
    green ">> Created .env from template"
else
    cyan ">> .env already exists, leaving it untouched"
fi

# ---- 5. Done -----------------------------------------------------------------
cat <<EOF

$(green "✓ zai-monitor installed at: $TARGET_DIR")

$(cyan "Next steps:")

  1. Edit .env and paste your coding plan API key:
       ZAI_API_KEY=xxxxxxxxxxxx.yyyyyyyyyyyyyyyy

  2. Launch the TUI:
       cd "$TARGET_DIR"
       source .venv/bin/activate
       python tui.py

  3. (optional) Configure Telegram alerts — create a bot with @BotFather,
     then run:
       python setup_telegram.py "YOUR:BOT_TOKEN"
     and add recipients in config.toml ([alerts] tg_chat_ids = [...])

  4. (optional) Run the alerts daemon in the background on macOS:
       cp launchd/ai.zai-monitor.alerts.plist ~/Library/LaunchAgents/
       # edit the plist to replace USERNAME with your user, then:
       launchctl load ~/Library/LaunchAgents/ai.zai-monitor.alerts.plist

  Get your API key from: https://z.ai/manage-apikey/apikey-list

EOF
