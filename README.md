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

- `credentials.json` / `credentials_web.json`
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
# для веб-OAuth (фича 4–5):
export NGROK_STATIC_DOMAIN='ваш-статический-домен.ngrok-free.app'
export GOOGLE_CLOUD_PROJECT_ID='your-gcp-project-id'
./run.sh
```

### Переменные окружения

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота (обязательно) |
| `NGROK_STATIC_DOMAIN` | статический домен ngrok для OAuth redirect |
| `GOOGLE_CLOUD_PROJECT_ID` | ID проекта GCP (ссылка на Test users при одобрении) |

### Настройка веб-OAuth (опционально)

1. Claim free static domain в [ngrok dashboard](https://dashboard.ngrok.com).
2. OAuth client типа **Web application** → redirect URI `https://<домен>/oauth2callback`.
3. Сохраните клиент как `credentials_web.json` (или пришлите боту файлом).
4. `export NGROK_STATIC_DOMAIN=<домен>`.

### Вариант A: файлы уже на диске

- `tokens/token_*.json` (достаточно для работы)
- опционально `credentials.json` / `credentials_web.json`

### Вариант B: первый запуск без почт

1. `/start` → владелец видит меню сразу.
2. Добавить почту: веб-OAuth («➕ Добавить аккаунт»), файл `token_*.json` или **tokens.zip**.

## Меню владельца

- **Все почты** — список; сверху «➕ Добавить аккаунт» (OAuth-ссылка), снизу «➕ Добавить почту» (файл `token_*.json`)
- **Триггеры** — уведомления о новых письмах (History API, ~30 с)
- **Активность** — сканирование по отправителю

Команды в меню Telegram: `/start`, `/new`.

## Онбординг пользователей

1. Гость: `/start` → вводит email → заявка владельцу.
2. Владелец: Test user в GCP → ✅ Подтвердить → пользователю одноразовая ссылка (2 ч).
3. После OAuth почта появляется у владельца; JSON-бэкап уходит владельцу.

Повторная регистрация той же почты (pending / approved / уже подключена) блокируется.
