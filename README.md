# Gmail Monitor Telegram Bot

Мониторинг нескольких Gmail-аккаунтов через Telegram (`python-telegram-bot` v20+).

## Роли

| Роль | Доступ |
|---|---|
| **owner** | Главное меню, все разделы, добавление аккаунтов, одобрение заявок |
| **contributor** | Только `/start` и `/new` (онбординг почт с подтверждением владельца) |
| **guest** | То же, что contributor, до одобрения заявки |

Первый, кто напишет `/start` при пустом `owner_id`, становится владельцем.

## Что не коммитить

- `credentials.json` / `credentials_desktop.json`
- `tokens/` — `token_*.json`
- `tokens.zip` / `uploads/`
- `data/bot_config.json`, `pending_registrations.json`, `history_state.json`

## Быстрый старт

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp data/bot_config.example.json data/bot_config.json

export TELEGRAM_BOT_TOKEN='токен_от_BotFather'
export GOOGLE_CLOUD_PROJECT_ID='your-gcp-project-id'
./run.sh
```

### Переменные окружения

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота (обязательно) |
| `GOOGLE_CLOUD_PROJECT_ID` | ID проекта GCP (ссылка на Test users при одобрении) |
| `CHECK_INTERVAL` | интервал опроса триггеров в секундах (по умолчанию `10`, диапазон 5–60) |

### Настройка Desktop OAuth (опционально)

Без туннеля и входящего порта. Google **Device Flow не поддерживает Gmail scopes** —
поэтому используется Desktop-клиент и вставка кода из браузера:

1. В Google Cloud Console создайте OAuth client ID типа **Desktop app** (не Web, не TVs).
2. Скачайте JSON → `credentials_desktop.json` в корень проекта (или пришлите боту файлом).
3. «➕ Добавить аккаунт» → откройте ссылку → после согласия скопируйте URL
   `http://127.0.0.1:8765/?code=...` из адресной строки (страница не откроется — так и должно быть) → пришлите боту.
4. Пока приложение не верифицировано, добавляйте Test users в Audience.

### Вариант A: файлы уже на диске

- `tokens/token_*.json` (достаточно для работы)
- опционально `credentials.json` / `credentials_desktop.json`

### Вариант B: единый `tokens.zip`

Пришлите боту один архив (плоский или с папкой `tokens/`):

```text
tokens.zip
├── credentials_desktop.json   # опционально — для «Добавить аккаунт»
├── credentials.json           # опционально
└── tokens/
    └── token_*.json           # обязательно ≥1
```

Переустановка zip **заменяет** все текущие `token_*.json`.

### Вариант C: первый запуск без почт

1. `/start` → владелец видит меню сразу.
2. Добавить почту: OAuth-ссылка («➕ Добавить аккаунт»), файл `token_*.json` или **tokens.zip**.

## Меню владельца

- **Все почты** — список; сверху «➕ Добавить аккаунт» (OAuth-ссылка), снизу «➕ Добавить почту» (файл `token_*.json`)
- **Триггеры** — уведомления о новых письмах (History API, ~10 с; env `CHECK_INTERVAL`)
- **Активность** — сканирование по отправителю

Команды в меню Telegram: `/start`, `/new`, `/clear` (сброс pending OAuth и открытых заявок).

## Онбординг пользователей

1. Гость: `/start` → вводит email → заявка владельцу.
2. Владелец: Test user в GCP → ✅ Подтвердить → пользователю ссылка OAuth + инструкция вставить код.
3. После OAuth почта появляется у владельца; JSON-бэкап уходит владельцу.

Повторная регистрация той же почты (pending / approved / уже подключена) блокируется.
