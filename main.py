import asyncio
import html
import logging
import os
import re
from io import BytesIO

from google.oauth2.credentials import Credentials as UserCredentials
from telegram import (
    Update,
    BotCommand,
    MenuButtonCommands,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
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
import oauth_desktop

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKENS_DIR = "tokens"
DATA_DIR = "data"
UPLOADS_DIR = "uploads"

EMAIL_RE = re.compile(r"^[\w.\-+]+@[\w\-]+\.[\w.\-]+$")


def get_role(update: Update, storage: Storage) -> str:
    user = update.effective_user
    if not user:
        return "unknown"
    user_id = user.id
    if storage.is_owner(user_id):
        return "owner"
    if storage.is_approved_contributor(user_id):
        return "contributor"
    return "guest"


def _setup_needed(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True when there are no Gmail accounts yet (soft — menu still available)."""
    return not context.bot_data.get("env_ready", False)


def _back_to_accounts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")]]
    )


def _refresh_env_ready(context_or_app) -> bool:
    ready = environment_ready()
    bot_data = (
        context_or_app.bot_data
        if hasattr(context_or_app, "bot_data")
        else context_or_app
    )
    bot_data["env_ready"] = ready
    return ready


def _maybe_start_jobs(application: Application) -> None:
    if not environment_ready():
        return
    application.bot_data["env_ready"] = True
    if not application.bot_data.get("jobs_started"):
        start_background_jobs(
            application,
            application.bot_data["gmail_client"],
            application.bot_data["storage"],
        )
        application.bot_data["jobs_started"] = True


async def _prompt_setup_hint(update: Update) -> None:
    text = (
        "⚙️ Пока нет подключённых почт.\n\n"
        "Варианты:\n"
        "• «Все почты» → «➕ Добавить аккаунт» (OAuth-ссылка)\n"
        "• «➕ Добавить почту» (файл token_*.json)\n"
        "• пришлите tokens.zip (credentials.json + token_*.json)"
    )
    target = update.effective_message
    if target:
        await target.reply_text(text)


async def _finish_oauth_tokens(
    bot,
    chat_id: str,
    gmail_client: GmailClient,
    storage: Storage,
    tokens: dict,
    client_id: str,
    client_secret: str,
    *,
    expected_email: str | None = None,
    notify_owner_on_success: bool = False,
    registration_id: str | None = None,
    application: Application | None = None,
) -> None:
    scope_raw = tokens.get("scope", oauth_desktop.SCOPES)
    scopes = scope_raw.split() if isinstance(scope_raw, str) else list(scope_raw)
    creds = UserCredentials(
        token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        token_uri=oauth_desktop.TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )
    raw_json = creds.to_json().encode("utf-8")

    ok, message, email = gmail_client.add_account_from_json_bytes(raw_json)
    if not ok:
        await bot.send_message(chat_id, f"❌ Не удалось подключить почту: {message}")
        return

    if expected_email and email.lower() != expected_email.lower():
        await bot.send_message(
            chat_id,
            f"⚠️ Вы авторизовались как {email}, а заявка была на {expected_email}. "
            "Почта всё равно подключена, но проверьте, что это правильный аккаунт.",
        )

    if registration_id:
        try:
            storage.update_registration_status(registration_id, "completed")
        except Exception:
            logger.exception("Failed to mark registration completed")

    if application is not None:
        _maybe_start_jobs(application)

    await bot.send_message(chat_id, f"✅ Почта {email} подключена ({message}).")

    filename = f"token_{email}.json"
    if notify_owner_on_success:
        owner_chat_id = storage.resolve_notify_chat_id()
        if owner_chat_id and str(owner_chat_id) != str(chat_id):
            await bot.send_message(
                owner_chat_id, f"✅ Пользователь подключил почту {email}."
            )
            await bot.send_document(
                owner_chat_id,
                document=InputFile(BytesIO(raw_json), filename=filename),
                caption="Резервная копия конфигурации.",
            )
    else:
        await bot.send_document(
            chat_id,
            document=InputFile(BytesIO(raw_json), filename=filename),
            caption=(
                "Резервная копия конфигурации — сохраните на случай сбоя сервера."
            ),
        )


async def start_oauth_link_flow(
    bot,
    chat_id: str,
    bot_data: dict,
    *,
    expected_email: str | None = None,
    notify_owner_on_success: bool = False,
    registration_id: str | None = None,
) -> None:
    """Send Desktop OAuth URL; user pastes redirect URL/code back into Telegram."""
    pending = bot_data.setdefault("pending_oauth", {})
    chat_key = str(chat_id)
    if chat_key in pending:
        await bot.send_message(
            chat_id, "⏳ Уже идёт авторизация, дождитесь её завершения или пришлите код."
        )
        return

    try:
        creds_data = oauth_desktop.load_desktop_client_creds()
    except Exception as e:
        await bot.send_message(
            chat_id, f"❌ Не удалось прочитать credentials_desktop.json: {e}"
        )
        return

    client_id = creds_data["client_id"]
    client_secret = creds_data.get("client_secret") or ""
    try:
        auth = oauth_desktop.build_authorization_url(client_id)
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Не удалось сформировать ссылку: {e}")
        return

    pending[chat_key] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": auth["code_verifier"],
        "state": auth["state"],
        "redirect_uri": auth["redirect_uri"],
        "expected_email": expected_email,
        "notify_owner_on_success": notify_owner_on_success,
        "registration_id": registration_id,
    }

    await bot.send_message(
        chat_id,
        "🔑 Откройте ссылку и войдите в Google-аккаунт:\n"
        f"{auth['auth_url']}\n\n"
        "После разрешения браузер откроет страницу "
        f"<code>{html.escape(oauth_desktop.REDIRECT_URI)}/?code=...</code> "
        "(сайт не откроется — это нормально).\n\n"
        "Скопируйте <b>весь адрес</b> из строки браузера и пришлите его сюда "
        "(или только значение параметра code=).",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _schedule_oauth_link(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    *,
    expected_email: str | None = None,
    notify_owner_on_success: bool = False,
    registration_id: str | None = None,
) -> bool:
    pending = context.bot_data.setdefault("pending_oauth", {})
    chat_key = str(chat_id)
    if chat_key in pending:
        return False
    asyncio.create_task(
        start_oauth_link_flow(
            context.bot,
            chat_key,
            context.bot_data,
            expected_email=expected_email,
            notify_owner_on_success=notify_owner_on_success,
            registration_id=registration_id,
        )
    )
    return True


async def handle_oauth_code_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """If this chat has pending OAuth, consume pasted code. Returns True if handled."""
    chat = update.effective_chat
    if not chat or not update.message:
        return False
    chat_key = str(chat.id)
    pending_map = context.bot_data.setdefault("pending_oauth", {})
    session = pending_map.get(chat_key)
    if not session:
        return False

    text = update.message.text or ""
    code = oauth_desktop.extract_auth_code(text)
    if not code:
        await update.message.reply_text(
            "Не вижу код авторизации. Пришлите полный URL из адресной строки "
            "(с code=...) или сам код после авторизации."
        )
        return True

    await update.message.reply_text("⏳ Обмениваю код на токен…")
    result = await oauth_desktop.exchange_code_for_tokens(
        session["client_id"],
        session["client_secret"],
        code,
        session["code_verifier"],
        session.get("redirect_uri", oauth_desktop.REDIRECT_URI),
    )
    if not result["ok"]:
        await update.message.reply_text(
            f"❌ Не удалось получить токен: {result['reason']}\n"
            "Попробуйте «➕ Добавить аккаунт» снова и пришлите свежий код."
        )
        pending_map.pop(chat_key, None)
        return True

    pending_map.pop(chat_key, None)
    try:
        await _finish_oauth_tokens(
            context.bot,
            chat_key,
            context.bot_data["gmail_client"],
            context.bot_data["storage"],
            result["tokens"],
            session["client_id"],
            session["client_secret"],
            expected_email=session.get("expected_email"),
            notify_owner_on_success=bool(session.get("notify_owner_on_success")),
            registration_id=session.get("registration_id"),
            application=context.application,
        )
    except Exception:
        logger.exception("OAuth finish failed for chat %s", chat_key)
        await update.message.reply_text("❌ Сбой после получения токена. Попробуйте снова.")
    return True


async def handle_guest_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, is_new: bool
) -> None:
    storage: Storage = context.bot_data["storage"]
    user = update.effective_user
    if not user or not update.message:
        return

    request = storage.get_registration_by_user(user.id)

    if is_new:
        await update.message.reply_text(
            "Введите почту, с которой будете регистрироваться:"
        )
        context.user_data["awaiting_registration_email"] = True
        return

    if request is None:
        await update.message.reply_text(
            "Введите почту, с которой будете регистрироваться:"
        )
        context.user_data["awaiting_registration_email"] = True
    elif request["status"] == "pending":
        await update.message.reply_text(
            "Ваша заявка уже на рассмотрении. Ожидайте подтверждения владельца."
        )
    elif request["status"] == "rejected":
        await update.message.reply_text(
            "Ваша предыдущая заявка была отклонена. "
            "Введите почту, чтобы подать новую заявку:"
        )
        context.user_data["awaiting_registration_email"] = True
    elif request["status"] == "approved":
        if not oauth_desktop.credentials_desktop_exists():
            await update.message.reply_text(
                "Заявка подтверждена, но на сервере нет credentials_desktop.json. "
                "Напишите владельцу."
            )
            return
        if not _schedule_oauth_link(
            context,
            str(request["chat_id"]),
            expected_email=request.get("email"),
            notify_owner_on_success=True,
            registration_id=request.get("id"),
        ):
            await update.message.reply_text(
                "⏳ Уже идёт авторизация — откройте ссылку выше или пришлите код из браузера."
            )
            return
        await update.message.reply_text(
            "✅ Ваша заявка уже подтверждена. Отправляю ссылку для авторизации…"
        )
    elif request["status"] == "completed":
        await update.message.reply_text(
            "Вы уже зарегистрированы. Введите /new, чтобы добавить ещё одну почту."
        )
    else:
        await update.message.reply_text(
            "Введите почту, с которой будете регистрироваться:"
        )
        context.user_data["awaiting_registration_email"] = True


async def start_add_account_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, via_message: bool = False
) -> None:
    """Owner: start Desktop OAuth account add (paste code from browser)."""
    query = update.callback_query
    reply = (
        (lambda text, **kw: query.edit_message_text(text, **kw))
        if query and not via_message
        else (lambda text, **kw: update.effective_message.reply_text(text, **kw))
    )
    chat = update.effective_chat
    if not chat:
        return
    chat_id = str(chat.id)

    if not oauth_desktop.credentials_desktop_exists():
        await reply(
            "⚠️ Не найден credentials_desktop.json.\n\n"
            "Создайте OAuth-клиент типа «Desktop app» в Google Cloud Console "
            "и пришлите файл credentials_desktop.json "
            "(credentials_device.json от TV-клиента для Gmail не подходит).",
            reply_markup=_back_to_accounts_kb() if query and not via_message else None,
        )
        context.user_data["waiting_for_credentials_desktop"] = True
        return

    pending = context.bot_data.setdefault("pending_oauth", {})
    if chat_id in pending:
        await reply(
            "⏳ Уже идёт авторизация — пришлите код/URL из браузера "
            "или дождитесь завершения.",
            reply_markup=_back_to_accounts_kb() if query and not via_message else None,
        )
        return

    await reply("⏳ Готовлю ссылку авторизации…")
    _schedule_oauth_link(context, chat_id, notify_owner_on_success=False)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get("storage")
    if not storage or not update.message:
        if update.message:
            await update.message.reply_text("Ошибка инициализации хранилища")
        return

    user = update.effective_user
    if not user:
        return

    if storage.get_owner_id() is None:
        storage.set_owner_id_if_empty(user.id)
        chat = update.effective_chat
        if chat:
            storage.ensure_owner_notify_chat(user.id, str(chat.id))
        logger.info("Bootstrapped owner_id=%s", user.id)

    _refresh_env_ready(context)
    role = get_role(update, storage)

    if role in ("contributor", "guest"):
        await handle_guest_flow(update, context, is_new=False)
        return

    await accounts.show_main_menu(update, context)
    if _setup_needed(context):
        await _prompt_setup_hint(update)


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get("storage")
    if not storage or not update.message:
        return

    user = update.effective_user
    if not user:
        return

    if storage.get_owner_id() is None:
        storage.set_owner_id_if_empty(user.id)

    role = get_role(update, storage)

    if role in ("contributor", "guest"):
        await handle_guest_flow(update, context, is_new=True)
        return

    await start_add_account_flow(update, context, via_message=True)


async def handle_approve_reg(
    update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str
) -> None:
    storage: Storage = context.bot_data["storage"]
    query = update.callback_query
    request_id = callback_data.split(":", 1)[1]
    request = storage.get_registration(request_id)
    if not request or request["status"] != "pending":
        await query.answer("Заявка уже обработана", show_alert=True)
        return

    await query.answer()

    if not oauth_desktop.credentials_desktop_exists():
        await query.edit_message_text(
            f"⚠️ Не найден credentials_desktop.json — нельзя запустить авторизацию "
            f"для {request['email']}.\n\n"
            "Заявка остаётся в статусе pending — пришлите Desktop OAuth JSON и нажмите "
            "✅ Подтвердить снова."
        )
        return

    pending = context.bot_data.setdefault("pending_oauth", {})
    if str(request["chat_id"]) in pending:
        await query.edit_message_text(
            "⏳ Для пользователя уже идёт авторизация. Дождитесь завершения "
            "и при необходимости нажмите ✅ снова (заявка пока pending)."
        )
        return

    storage.update_registration_status(request_id, "approved")
    storage.ensure_user_allowed_contributor(request["user_id"])
    await query.edit_message_text(
        f"✅ Подтверждено: {request['email']}\nОтправляю ссылку авторизации пользователю…"
    )
    _schedule_oauth_link(
        context,
        str(request["chat_id"]),
        expected_email=request["email"],
        notify_owner_on_success=True,
        registration_id=request_id,
    )


async def handle_reject_reg(
    update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str
) -> None:
    storage: Storage = context.bot_data["storage"]
    query = update.callback_query
    request_id = callback_data.split(":", 1)[1]
    request = storage.get_registration(request_id)
    if not request or request["status"] != "pending":
        await query.answer("Заявка уже обработана", show_alert=True)
        return

    await query.answer()
    storage.update_registration_status(request_id, "rejected")
    try:
        await context.bot.send_message(
            request["chat_id"], "❌ Ваша заявка отклонена владельцем."
        )
    except Exception as e:
        logger.warning("Failed to notify rejected user: %s", e)
    await query.edit_message_text(f"❌ Отклонено: {request['email']}")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get("storage")
    if not storage:
        await update.callback_query.answer("Ошибка инициализации хранилища")
        return

    query = update.callback_query
    role = get_role(update, storage)
    callback_data = query.data or ""

    if role != "owner":
        await query.answer("Доступ запрещён", show_alert=True)
        return

    _refresh_env_ready(context)

    # approve/reject answer themselves (alert path vs normal)
    if callback_data.startswith("approve_reg:"):
        await handle_approve_reg(update, context, callback_data)
        return
    if callback_data.startswith("reject_reg:"):
        await handle_reject_reg(update, context, callback_data)
        return

    await query.answer()

    if callback_data == "main_menu":
        await accounts.show_main_menu(update, context)
    elif callback_data.startswith("accounts_page:"):
        await accounts.show_accounts_page(update, context, callback_data)
    elif callback_data == "add_account":
        await start_add_account_flow(update, context)
    elif callback_data == "add_email_file":
        await accounts.start_add_email_file(update, context)
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


async def _handle_registration_email(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    storage: Storage = context.bot_data["storage"]
    gmail_client: GmailClient = context.bot_data["gmail_client"]
    email = (update.message.text or "").strip()

    if not EMAIL_RE.match(email):
        await update.message.reply_text("Это не похоже на email. Введите его ещё раз:")
        return

    email_norm = email.lower()

    if gmail_client.has_account(email_norm):
        await update.message.reply_text(
            "❌ Эта почта уже подключена к боту. Укажите другую."
        )
        return

    connected = set(gmail_client.get_all_accounts().keys())
    blocked = storage.is_email_registration_blocked(
        email_norm, connected_emails=connected
    )
    if blocked:
        await update.message.reply_text(f"❌ {blocked}")
        return

    owner_chat_id = storage.resolve_notify_chat_id()
    if not owner_chat_id:
        await update.message.reply_text(
            "❌ Владелец бота ещё не настроен (нет chat id). Попробуйте позже."
        )
        return

    context.user_data["awaiting_registration_email"] = False

    user = update.effective_user
    chat = update.effective_chat
    request_id = storage.add_registration_request(user.id, str(chat.id), email_norm)

    await update.message.reply_text("Почта принята! Ожидайте проверки владельца.")

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT_ID", "")
    audience_link = (
        f"https://console.cloud.google.com/auth/audience?project={project_id}"
        if project_id
        else "(укажите GOOGLE_CLOUD_PROJECT_ID в .env)"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_reg:{request_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_reg:{request_id}"),
        ]
    ]
    try:
        await context.bot.send_message(
            owner_chat_id,
            f"📨 Новый запрос на регистрацию:\n{email_norm}\n"
            f"Пользователь: {user.full_name} (id {user.id})\n\n"
            f"Перед подтверждением добавьте эту почту как Test user здесь:\n{audience_link}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.exception("Failed to notify owner about registration")
        await update.message.reply_text(
            f"Заявка сохранена, но владелец не получил уведомление: {e}"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get("storage")
    if not storage or not update.message:
        return

    role = get_role(update, storage)
    user_data = context.user_data

    if user_data.get("awaiting_registration_email"):
        await _handle_registration_email(update, context)
        return

    # OAuth paste (owner or contributor with pending session)
    if await handle_oauth_code_message(update, context):
        return

    if role != "owner":
        if role in ("contributor", "guest"):
            await handle_guest_flow(update, context, is_new=False)
        else:
            await update.message.reply_text("Доступ запрещён")
        return

    if user_data.get("waiting_for_trigger_input"):
        await triggers.process_trigger_input(update, context)
    elif user_data.get("waiting_for_trigger_edit"):
        await triggers.process_trigger_edit_input(update, context)
    elif user_data.get("waiting_for_activity_sender"):
        await activity.process_sender_input(update, context)
    elif user_data.get("waiting_for_token_json") or user_data.get(
        "waiting_for_credentials_desktop"
    ):
        await update.message.reply_text("Ожидается файл, а не текст. Пришлите документ.")
    elif _setup_needed(context):
        await _prompt_setup_hint(update)
    else:
        await update.message.reply_text("Используйте кнопки меню для навигации")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get("storage")
    if not storage or not update.message or not update.message.document:
        return

    role = get_role(update, storage)
    if role != "owner":
        await update.message.reply_text("Доступ запрещён")
        return

    doc = update.message.document
    filename = doc.file_name or ""
    filename_lower = filename.lower()
    gmail_client: GmailClient = context.bot_data["gmail_client"]

    if (
        filename_lower in ("credentials_desktop.json", "credentials_device.json")
        or context.user_data.get("waiting_for_credentials_desktop")
    ):
        if filename_lower not in (
            "credentials_desktop.json",
            "credentials_device.json",
        ):
            await update.message.reply_text(
                "Нужен файл credentials_desktop.json (OAuth Desktop app)."
            )
            return
        dest = project_root() / "credentials_desktop.json"
        try:
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(custom_path=str(dest))
        except Exception as e:
            logger.exception("Failed to save credentials_desktop.json")
            await update.message.reply_text(f"❌ Не удалось сохранить файл: {e}")
            return
        context.user_data["waiting_for_credentials_desktop"] = False
        await update.message.reply_text(
            "✅ credentials_desktop.json сохранён. Нажмите «➕ Добавить аккаунт» снова.\n"
            "Нужен клиент типа Desktop app (не TVs / Limited Input)."
        )
        return

    if context.user_data.get("waiting_for_token_json") or (
        filename_lower.startswith("token_") and filename_lower.endswith(".json")
    ):
        if not (filename_lower.startswith("token_") and filename_lower.endswith(".json")):
            await update.message.reply_text(
                "Нужен файл token_*.json. Пришлите корректный файл."
            )
            return

        uploads = project_root() / UPLOADS_DIR
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / f"upload_{update.effective_user.id}_{filename}"
        raw = b""

        try:
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(custom_path=str(dest))
            raw = dest.read_bytes()
        except Exception as e:
            logger.exception("Failed to download token json")
            await update.message.reply_text(f"❌ Не удалось скачать файл: {e}")
            return
        finally:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass

        ok, message, email = gmail_client.add_account_from_json_bytes(raw)
        context.user_data["waiting_for_token_json"] = False

        if not ok:
            await update.message.reply_text(f"❌ Не удалось добавить почту: {message}")
            return

        _maybe_start_jobs(context.application)
        await update.message.reply_text(f"✅ Почта {email} подключена ({message}).")
        keyboard = [
            [InlineKeyboardButton("📧 К списку почт", callback_data="accounts_page:0")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
        ]
        await update.message.reply_text(
            "Готово.", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    wants_setup = (
        _setup_needed(context)
        or context.user_data.get("waiting_for_tokens_zip")
        or filename_lower.endswith(".zip")
    )
    if not wants_setup:
        await update.message.reply_text(
            "Сейчас ожидаются token_*.json, credentials_desktop.json или tokens.zip."
        )
        return

    if not filename_lower.endswith(".zip"):
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
        await update.message.reply_text(
            f"❌ {message}\n\nПришлите корректный tokens.zip ещё раз."
        )
        return

    await update.message.reply_text("🔄 Загружаю Gmail-аккаунты…")
    try:
        gmail_client.reload_accounts()
    except Exception as e:
        logger.exception("reload_accounts failed")
        await update.message.reply_text(f"❌ Ошибка загрузки аккаунтов: {e}")
        return

    if not gmail_client.get_all_accounts():
        await update.message.reply_text(
            "⚠️ Файлы установлены, но активных аккаунтов не загружено. "
            "Проверьте валидность token_*.json."
        )
        return

    user = update.effective_user
    chat = update.effective_chat
    storage.ensure_owner_notify_chat(
        user.id, chat_id=str(chat.id) if chat else str(user.id)
    )

    _maybe_start_jobs(context.application)

    count = len(gmail_client.get_all_accounts())
    await update.message.reply_text(
        f"✅ Готово: {message}\nАккаунтов загружено: {count}"
    )
    await accounts.show_main_menu(update, context)


async def _post_init(application: Application) -> None:
    try:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Открыть меню бота"),
                BotCommand("new", "Добавить ещё одну почту"),
            ]
        )
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Registered bot commands and menu button")
    except Exception:
        logger.exception("Failed to set bot commands / menu button")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set")
        return

    storage = Storage(DATA_DIR)
    ready = environment_ready()
    status = environment_status()
    logger.info(
        "Environment: ready=%s credentials=%s credentials_desktop=%s tokens=%s owner=%s",
        status["ready"],
        status["has_credentials"],
        status.get("has_credentials_desktop"),
        status["token_count"],
        storage.get_owner_id(),
    )

    gmail_client = GmailClient(TOKENS_DIR, auto_load=ready)

    application = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .build()
    )

    application.bot_data["gmail_client"] = gmail_client
    application.bot_data["storage"] = storage
    application.bot_data["env_ready"] = ready
    application.bot_data["jobs_started"] = False
    application.bot_data["pending_oauth"] = {}

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("new", new_command))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    if ready:
        start_background_jobs(application, gmail_client, storage)
        application.bot_data["jobs_started"] = True
    else:
        logger.warning(
            "No tokens yet — owner can add via OAuth link, token file, or tokens.zip"
        )

    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
