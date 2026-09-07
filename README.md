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

- `credentials.json` / `credentials_device.json`
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

### Настройка Device OAuth (опционально)

OAuth **Device Authorization Grant** (RFC 8628) — без туннеля, порта и домена.
Бот показывает код → пользователь вводит его на https://www.google.com/device с любого устройства.

1. В Google Cloud Console создайте OAuth client ID типа **TVs and Limited Input devices** (не Web application).
2. Скачайте JSON и сохраните как `credentials_device.json` в корень проекта (или пришлите боту файлом).
3. Пока приложение не верифицировано, добавляйте Test users в Audience.

### Вариант A: файлы уже на диске

- `tokens/token_*.json` (достаточно для работы)
- опционально `credentials.json` / `credentials_device.json`

### Вариант B: первый запуск без почт

1. `/start` → владелец видит меню сразу.
2. Добавить почту: Device OAuth («➕ Добавить аккаунт»), файл `token_*.json` или **tokens.zip**.

## Меню владельца

- **Все почты** — список; сверху «➕ Добавить аккаунт» (код Google device), снизу «➕ Добавить почту» (файл `token_*.json`)
- **Триггеры** — уведомления о новых письмах (History API, ~30 с)
- **Активность** — сканирование по отправителю

Команды в меню Telegram: `/start`, `/new`.

## Онбординг пользователей

1. Гость: `/start` → вводит email → заявка владельцу.
2. Владелец: Test user в GCP → ✅ Подтвердить → пользователю приходит код для https://www.google.com/device.
3. После OAuth почта появляется у владельца; JSON-бэкап уходит владельцу.

Повторная регистрация той же почты (pending / approved / уже подключена) блокируется.
