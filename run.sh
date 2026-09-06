#!/bin/bash

# Gmail Monitor Bot Run Script

cd "$(dirname "$0")"

# Use project venv if present
if [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
    PYTHON=python
elif [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PYTHON=python
else
    PYTHON=python3
fi

# Stop other instances of this bot to avoid Telegram 409 Conflict
EXISTING=$(pgrep -f "$(pwd)/.*main.py|python[3]? main.py" 2>/dev/null || true)
# More precise: kill only gmail_bot main.py in this directory
pkill -f "python[3]? main.py" 2>/dev/null || true
sleep 1

# Check if TELEGRAM_BOT_TOKEN is set
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "❌ ERROR: TELEGRAM_BOT_TOKEN environment variable is not set!"
    echo ""
    echo "Please set it before running the bot:"
    echo "  export TELEGRAM_BOT_TOKEN='your_bot_token_here'"
    echo ""
    echo "Or run with the token inline:"
    echo "  TELEGRAM_BOT_TOKEN='your_token' ./run.sh"
    echo ""
    exit 1
fi

# Check if dependencies are installed
echo "Checking dependencies..."
$PYTHON -c "import telegram" 2>/dev/null || { echo "❌ python-telegram-bot not installed. Run: source venv/bin/activate && pip install -r requirements.txt" >&2; exit 1; }
$PYTHON -c "import google.oauth2" 2>/dev/null || { echo "❌ Google Auth libraries not installed." >&2; exit 1; }

# Check if config files exist
if [ ! -f "data/bot_config.json" ]; then
    echo "❌ ERROR: data/bot_config.json not found!"
    echo "Run ./setup.sh first to create default configuration."
    exit 1
fi

# Check if tokens exist
if [ ! "$(ls -A tokens/ 2>/dev/null)" ]; then
    echo "⚠️  WARNING: No token files found in tokens/ directory!"
    echo "The bot will start but won't have any Gmail accounts to monitor."
    echo ""
fi

echo "🚀 Starting Gmail Monitor Bot (single instance)..."
echo "⏳ Loading Gmail accounts may take 1–2 minutes..."
echo ""
exec $PYTHON main.py
