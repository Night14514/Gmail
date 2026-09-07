#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

echo "📧 Gmail Monitor Bot Setup"
echo "=========================="

python3 --version

if [ -d venv ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
elif [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  python3 -m venv venv
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

pip install -r requirements.txt

mkdir -p tokens data uploads

if [ ! -f data/bot_config.json ]; then
  cp data/bot_config.example.json data/bot_config.json
  echo "⚠️  Создан data/bot_config.json — owner_id заполнится при первом /start"
fi

if [ ! -f data/pending_registrations.json ]; then
  echo '[]' > data/pending_registrations.json
fi

if [ ! -f credentials.json ] || [ -z "$(ls tokens/token_*.json 2>/dev/null)" ]; then
  echo "ℹ️  credentials.json / tokens пока нет."
  echo "   После запуска бота пришлите tokens.zip в чат, либо положите файлы вручную."
fi

echo "✅ Setup complete. Далее: export TELEGRAM_BOT_TOKEN=... && ./run.sh"
