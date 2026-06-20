#!/usr/bin/env bash
# zai-monitor installer — clones/updates the repo, sets up a venv, installs
# deps, and optionally configures one or more accounts (API keys).
#
# One-liner:
#   curl -fsSL https://raw.githubusercontent.com/jpalmae/zai-monitor/main/install.sh | bash
#
# Update existing install:
#   curl -fsSL https://raw.githubusercontent.com/jpalmae/zai-monitor/main/install.sh | bash
#
# Custom target dir / non-interactive (skip account prompts):
#   curl -fsSL https://raw.githubusercontent.com/jpalmae/zai-monitor/main/install.sh | bash -s -- ~/dev/zai
#
# Run locally:
#   bash install.sh [target_dir]

set -euo pipefail

REPO="https://github.com/jpalmae/zai-monitor.git"
TARGET_DIR="${1:-$HOME/zai-monitor}"

cyan()  { printf "\033[36m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*" >&2; }
die()   { red "error: $*"; exit 1; }

# Read a value from the controlling terminal (works under `curl | bash` where
# stdin is the script itself). Prints prompt to stderr. $1=varname $2=prompt
# $3=default. Non-interactive shells get the default.
ask() {
    local var="$1" prompt="$2" default="${3:-}" val=""
    if [ -r /dev/tty ]; then
        printf "%s" "$prompt" >&2
        IFS= read -r val < /dev/tty || val=""
        val="${val:-$default}"
    else
        val="$default"
    fi
    printf -v "$var" "%s" "$val"
}

# Portable in-place env var setter (no GNU/BSD sed differences).
set_env_var() {
    local key="$1" val="$2" file="$3" tmp
    tmp="$(mktemp)"
    grep -v "^${key}=" "$file" > "$tmp" 2>/dev/null || true
    printf "%s=%s\n" "$key" "$val" >> "$tmp"
    mv "$tmp" "$file"
}

# ---- 1. Check Python ---------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 not found. Install Python 3.10+ from https://python.org"
PY_VER=$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys;print(1 if sys.version_info>=(3,10) else 0)')
[ "$PY_OK" = "1" ] || die "Python 3.10+ required (found $PY_VER)."
cyan ">> Python $PY_VER OK"

# ---- 2. Clone or update ------------------------------------------------------
if [ -d "$TARGET_DIR/.git" ]; then
    cyan ">> Updating existing checkout at $TARGET_DIR"
    git -C "$TARGET_DIR" fetch origin
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

# ---- 5. Interactive account setup -------------------------------------------
configure_accounts() {
    echo "" >&2
    if [ ! -t 1 ]; then
        cyan ">> Non-interactive shell: skipping account setup."
        return 0
    fi
    printf "Do you want to configure account(s) now? [Y/n] " >&2
    IFS= read -r ans < /dev/tty || ans=""
    case "$ans" in
        n|N) cyan ">> Skipping account setup."; return 0 ;;
    esac

    ask n "How many accounts do you want to monitor? [1] " "1"
    case "$n" in
        ''|*[!0-9]*) n=1 ;;
    esac
    if [ "$n" -lt 1 ]; then n=1; fi

    if [ "$n" -eq 1 ]; then
        echo "" >&2
        yellow "Account #1"
        ask name "  Name (optional, e.g. Personal): " ""
        ask key  "  API key (xxxxxxxxxxxx.yyyyyyyyyyyyyyyy): " ""
        if [ -z "$key" ]; then
            red "  No API key entered; leaving .env untouched."
            return 0
        fi
        set_env_var "ZAI_API_KEY" "$key" .env
        if [ -n "$name" ]; then
            set_env_var "ZAI_ACCOUNT_NAME" "$name" .env
        fi
        green "  -> wrote account to .env"
    else
        : > accounts.toml
        echo "# Local multi-account config (gitignored)" > accounts.toml
        i=1
        while [ "$i" -le "$n" ]; do
            echo "" >&2
            yellow "Account #$i"
            ask name "  Name: " "account$i"
            ask key  "  API key: " ""
            if [ -n "$key" ]; then
                {
                    printf '\n[[accounts]]\n'
                    printf 'name    = "%s"\n' "$name"
                    printf 'api_key = "%s"\n' "$key"
                } >> accounts.toml
                green "  -> added $name"
            else
                red "  -> skipped (no key)"
            fi
            i=$((i + 1))
        done
        green "  -> wrote $n account block(s) to accounts.toml"
    fi

    # Validate by doing a live fetch with the venv python.
    echo "" >&2
    cyan ">> Validating accounts (live fetch)..."
    if python -c "
import config, fetcher
accts = config.load_accounts()
if not accts:
    raise SystemExit('no accounts configured')
for a in accts:
    try:
        s = fetcher.fetch_snapshot(api_key=a.api_key)
        lvl = (s.level or '?').upper()
        print(f'  OK  {a.name or \"(unnamed)\":<16} {lvl}  5h={s.get(\"five_hour\").percentage}% weekly={s.get(\"weekly\").percentage}%')
    except Exception as e:
        print(f'  ERR {a.name or \"(unnamed)\":<16} {e}')
" 2>&1; then
        green ">> Validation done"
    else
        yellow ">> Validation had issues — you can fix .env / accounts.toml and re-run."
    fi
}

configure_accounts

# ---- 6. Done -----------------------------------------------------------------
cat <<EOF

$(green "✓ zai-monitor installed/updated at: $TARGET_DIR")

$(cyan "Next steps:")

  1. Launch the TUI:
       cd "$TARGET_DIR"
       source .venv/bin/activate
       python tui.py

  2. (optional) Configure Telegram alerts — create a bot with @BotFather,
     then run:
       python setup_telegram.py "YOUR:BOT_TOKEN"
     and add recipients in config.toml ([alerts] tg_chat_ids = [...])

  3. (optional) Run the alerts daemon in the background:
       macOS:   see README -> "Daemon en background (macOS)"
       Linux:   see README -> "Daemon en background (Linux / Ubuntu 24.04+)"

  Get your API key from: https://z.ai/manage-apikey/apikey-list
  Re-run this installer any time to UPDATE: it pulls and reinstalls cleanly.

EOF
