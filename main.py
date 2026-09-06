import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from gmail_client import GmailClient
from storage import Storage
from handlers import accounts, triggers, activity
from background_jobs import start_background_jobs
from setup_env import environment_ready, environment_status, install_tokens_zip, project_root

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKENS_DIR = "tokens"
DATA_DIR = "data"
UPLOADS_DIR = "uploads"


def check_user_allowed(update: Update, storage: Storage) -> bool:
    if not update.effective_user:
        return False
    user_id = update.effective_user.id
    if not storage.is_user_allowed(user_id):
        logger.warning(f"Access denied for user {user_id}")
        return False
    return True


def _setup_needed(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return not context.bot_data.get('env_ready', False)


async def _prompt_setup(update: Update) -> None:
    text = (
        "⚙️ Первоначальная настройка\n\n"
        "Не найдены `credentials.json` и/или файлы в `tokens/`.\n\n"
        "Пришлите архив **tokens.zip**, внутри которого:\n"
        "• `credentials.json`\n"
        "• файлы `token_*.json` (можно в папке `tokens/`)\n\n"
        "После распаковки бот загрузит аккаунты и откроет меню."
    )
    target = update.effective_message
    if target:
        await target.reply_text(text, parse_mode="Markdown")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        await update.message.reply_text("Ошибка инициализации хранилища")
        return

    if not check_user_allowed(update, storage):
        await update.message.reply_text("Доступ запрещён")
        return

    if _setup_needed(context):
        context.user_data['waiting_for_tokens_zip'] = True
        await _prompt_setup(update)
        return

    await accounts.show_main_menu(update, context)


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        await update.callback_query.answer("Ошибка инициализации хранилища")
        return

    if not check_user_allowed(update, storage):
        await update.callback_query.answer("Доступ запрещён")
        return

    if _setup_needed(context):
        await update.callback_query.answer()
        context.user_data['waiting_for_tokens_zip'] = True
        await _prompt_setup(update)
        return

    query = update.callback_query
    await query.answer()

    callback_data = query.data

    if callback_data == "main_menu":
        await accounts.show_main_menu(update, context)
    elif callback_data.startswith("accounts_page:"):
        await accounts.show_accounts_page(update, context, callback_data)
    elif callback_data.startswith("account:"):
        await accounts.show_account_messages(update, context, callback_data)
    elif callback_data.startswith("msglist:"):
        await accounts.show_messages_page(update, context, callback_data)
    elif callback_data.startswith("msg:"):
        await accounts.show_message_full(update, context, callback_data)
    elif callback_data.startswith("btm:"):
        await accounts.show_account_messages(
            update, context, callback_data.replace("btm:", "account:", 1)
        )
    elif callback_data.startswith("triggers_menu"):
        await triggers.show_triggers_menu(update, context, callback_data)
    elif callback_data.startswith("trigger:"):
        await triggers.show_trigger_details(update, context, callback_data)
    elif callback_data.startswith("trigger_edit:"):
        await triggers.start_edit_trigger(update, context, callback_data)
    elif callback_data.startswith("trigger_delete:"):
        await triggers.confirm_delete_trigger(update, context, callback_data)
    elif callback_data.startswith("trigger_delete_confirm:"):
        await triggers.delete_trigger(update, context, callback_data)
    elif callback_data == "add_trigger":
        await triggers.start_add_trigger(update, context)
    elif callback_data == "activity_menu":
        await activity.show_activity_menu(update, context)
    elif callback_data.startswith("activity:"):
        await activity.show_sender_activity(update, context, callback_data)
    elif callback_data == "add_activity_sender":
        await activity.start_add_sender(update, context)
    elif callback_data == "acts":
        await activity.show_sender_hub_callback(update, context)
    elif callback_data == "actscan":
        await activity.scan_all_accounts_activity(update, context)
    elif callback_data.startswith("actpg:"):
        await activity.show_activity_scan_page(update, context, callback_data)
    elif callback_data.startswith("sfm:"):
        await activity.show_full_message(update, context, callback_data)
    else:
        await query.edit_message_text("Неизвестная команда")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        await update.message.reply_text("Ошибка инициализации хранилища")
        return

    if not check_user_allowed(update, storage):
        await update.message.reply_text("Доступ запрещён")
        return

    if _setup_needed(context):
        context.user_data['waiting_for_tokens_zip'] = True
        await _prompt_setup(update)
        return

    user_data = context.user_data

    if user_data.get('waiting_for_trigger_input'):
        await triggers.process_trigger_input(update, context)
    elif user_data.get('waiting_for_trigger_edit'):
        await triggers.process_trigger_edit_input(update, context)
    elif user_data.get('waiting_for_activity_sender'):
        await activity.process_sender_input(update, context)
    else:
        await update.message.reply_text("Используйте кнопки меню для навигации")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get('storage')
    if not storage or not update.message or not update.message.document:
        return

    if not check_user_allowed(update, storage):
        await update.message.reply_text("Доступ запрещён")
        return

    doc = update.message.document
    filename = (doc.file_name or "").lower()
    wants_setup = (
        _setup_needed(context)
        or context.user_data.get('waiting_for_tokens_zip')
        or filename.endswith(".zip")
    )
    if not wants_setup:
        await update.message.reply_text("Сейчас ожидаются только текстовые ответы или кнопки меню.")
        return

    if not filename.endswith(".zip"):
        await update.message.reply_text("Нужен файл tokens.zip (ZIP-архив).")
        return

    await update.message.reply_text("📥 Получил архив, распаковываю…")

    uploads = project_root() / UPLOADS_DIR
    uploads.mkdir(parents=True, exist_ok=True)
    dest = uploads / f"tokens_{update.effective_user.id}.zip"

    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(custom_path=str(dest))
    except Exception as e:
        logger.exception("Failed to download zip")
        await update.message.reply_text(f"❌ Не удалось скачать файл: {e}")
        return

    ok, message = install_tokens_zip(dest)
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass

    if not ok:
        await update.message.reply_text(f"❌ {message}\n\nПришлите корректный tokens.zip ещё раз.")
        return

    # Reload Gmail accounts
    gmail_client: GmailClient = context.bot_data['gmail_client']
    await update.message.reply_text("🔄 Загружаю Gmail-аккаунты…")
    try:
        gmail_client.reload_accounts()
    except Exception as e:
        logger.exception("reload_accounts failed")
        await update.message.reply_text(f"❌ Ошибка загрузки аккаунтов: {e}")
        return

    status = environment_status()
    if not status["ready"] or not gmail_client.get_all_accounts():
        await update.message.reply_text(
            "⚠️ Файлы установлены, но активных аккаунтов не загружено. "
            "Проверьте валидность token_*.json."
        )
        return

    context.bot_data['env_ready'] = True
    context.user_data['waiting_for_tokens_zip'] = False

    user = update.effective_user
    chat = update.effective_chat
    storage.ensure_user_allowed(user.id, notify_chat_id=str(chat.id) if chat else str(user.id))

    # Start background jobs once environment becomes ready
    if not context.bot_data.get('jobs_started'):
        start_background_jobs(
            context.application,
            gmail_client,
            storage,
        )
        context.bot_data['jobs_started'] = True

    count = len(gmail_client.get_all_accounts())
    await update.message.reply_text(
        f"✅ Готово: {message}\nАккаунтов загружено: {count}"
    )
    await accounts.show_main_menu(update, context)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set")
        return

    storage = Storage(DATA_DIR)
    ready = environment_ready()
    status = environment_status()
    logger.info(
        "Environment: ready=%s credentials=%s tokens=%s",
        status["ready"],
        status["has_credentials"],
        status["token_count"],
    )

    gmail_client = GmailClient(TOKENS_DIR, auto_load=ready)

    application = Application.builder().token(token).build()

    application.bot_data['gmail_client'] = gmail_client
    application.bot_data['storage'] = storage
    application.bot_data['env_ready'] = ready
    application.bot_data['jobs_started'] = False

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if ready:
        start_background_jobs(application, gmail_client, storage)
        application.bot_data['jobs_started'] = True
    else:
        logger.warning(
            "credentials.json / tokens missing — bot will wait for tokens.zip from an allowed user"
        )

    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
