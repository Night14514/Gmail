# Gmail Monitor Telegram Bot

Мониторинг нескольких Gmail-аккаунтов через Telegram (`python-telegram-bot` v20+).

## Что не коммитить

В репозиторий **не** попадают:

- `credentials.json` — OAuth client Google
- `tokens/` — файлы `token_*.json` аккаунтов
- `tokens.zip` / `uploads/`
- `data/bot_config.json` — ваши Telegram ID

Секреты ставятся локально или через архив при первом запуске.

## Быстрый старт

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp data/bot_config.example.json data/bot_config.json
# укажите свой Telegram user id в allowed_user_ids и notify_chat_id

export TELEGRAM_BOT_TOKEN='токен_от_BotFather'
./run.sh
```

### Вариант A: файлы уже на диске

Положите рядом с `main.py`:

- `credentials.json`
- `tokens/token_*.json`

### Вариант B: первый запуск без файлов

1. Запустите бота без `credentials.json` / `tokens/`.
2. Напишите `/start`.
3. Пришлите **tokens.zip**, внутри которого:
   - `credentials.json` (в корне или в любой папке)
   - один или несколько `token_*.json` (можно в папке `tokens/`)
4. Бот распакует архив, загрузит аккаунты и откроет меню.

Пример структуры архива:

```text
tokens.zip
├── credentials.json
└── tokens/
    ├── token_account0.json
    └── token_account1.json
```

## Меню

- **Все почты** — список аккаунтов и писем
- **Триггеры** — уведомления о новых письмах от отправителей
- **Активность** — выбор отправителя → кнопка **«По всем почтам (по дате)»**:
  сканирует все аккаунты, берёт последнее письмо от отправителя в каждом,
  показывает список, отсортированный по `internalDate` (новые сверху)

## Переменные окружения

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота (обязательно) |

## Структура

```text
main.py
gmail_client.py
storage.py
setup_env.py
background_jobs.py
handlers/
data/                 # локальные JSON (часть в .gitignore)
credentials.json      # локально / из zip
tokens/               # локально / из zip
```
