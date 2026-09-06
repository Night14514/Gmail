import re
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers.accounts import (
    extract_sender_info,
    extract_subject,
    extract_date,
    extract_text_from_message,
    truncate_text,
)

ACTIVITY_SCAN_WORKERS = 8
ACTIVITY_PAGE_SIZE = 8


def parse_sender_input(text: str) -> tuple[str, str]:
    text = text.strip()

    if "•" in text:
        parts = text.split("•", 1)
        name = parts[0].strip()
        email = parts[1].strip()
    elif "@" in text:
        email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
        if email_match:
            email = email_match.group()
            name = email.split('@')[0].capitalize()
        else:
            return "Без имени", text
    else:
        return "Без имени", text

    return name, email


def _format_ts(internal_date_ms: int) -> str:
    if not internal_date_ms:
        return "—"
    dt = datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)
    return dt.strftime("%d.%m.%Y %H:%M UTC")


async def show_activity_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации хранилища")
        return

    if update.callback_query:
        pass  # answered in main router

    known_senders = storage.get_known_senders()
    context.user_data['activity_senders'] = [
        {"name": s.sender_name, "email": s.sender_email} for s in known_senders
    ]

    keyboard = []

    if known_senders:
        for idx, sender in enumerate(known_senders):
            keyboard.append([
                InlineKeyboardButton(
                    f"{sender.sender_name} ({sender.sender_email})",
                    callback_data=f"activity:{idx}"
                )
            ])

    keyboard.append([
        InlineKeyboardButton("➕ Указать другого отправителя", callback_data="add_activity_sender")
    ])
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if known_senders:
        text = "📊 Активность отправителей\n\nВыберите отправителя для анализа:"
    else:
        text = "📊 Активность отправителей\n\nНет сохранённых отправителей"

    await update.callback_query.edit_message_text(text, reply_markup=reply_markup)


async def start_add_sender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data['waiting_for_activity_sender'] = True

    if update.callback_query:
        await update.callback_query.answer()

    keyboard = [
        [InlineKeyboardButton("🔙 Отмена", callback_data="activity_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "➕ Указать отправителя\n\nВведите email отправителя:\n\nПример: user@example.com",
        reply_markup=reply_markup
    )


async def process_sender_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        await update.message.reply_text("Ошибка инициализации хранилища")
        return

    user_input = update.message.text
    sender_name, sender_email = parse_sender_input(user_input)

    if "@" not in sender_email:
        await update.message.reply_text(
            "❌ Неверный формат email. Попробуйте ещё раз или нажмите кнопку отмены.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Отмена", callback_data="activity_menu")]
            ])
        )
        return

    context.user_data['waiting_for_activity_sender'] = False
    storage.add_known_sender(sender_name, sender_email)
    context.user_data['activity_sender_email'] = sender_email
    context.user_data['activity_sender_name'] = sender_name

    await show_sender_hub(update, context, sender_email, from_input=True)


async def show_sender_activity(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    try:
        idx = int(callback_data.split(":", 1)[1])
    except (IndexError, ValueError):
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка разбора данных")
        return

    senders = context.user_data.get('activity_senders') or []
    if idx < 0 or idx >= len(senders):
        storage = context.bot_data.get('storage')
        if storage:
            known = storage.get_known_senders()
            senders = [{"name": s.sender_name, "email": s.sender_email} for s in known]
            context.user_data['activity_senders'] = senders
        if idx < 0 or idx >= len(senders):
            await update.callback_query.edit_message_text("Отправитель не найден")
            return

    sender = senders[idx]
    context.user_data['activity_sender_email'] = sender["email"]
    context.user_data['activity_sender_name'] = sender["name"]
    await show_sender_hub(update, context, sender["email"], from_input=False)


async def show_sender_hub(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    sender_email: str,
    from_input: bool = False,
) -> None:
    name = context.user_data.get('activity_sender_name') or sender_email
    keyboard = [
        [InlineKeyboardButton(
            "📋 По всем почтам (по дате)",
            callback_data="actscan"
        )],
        [InlineKeyboardButton("🔙 К активности", callback_data="activity_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"📊 Активность: {name}\n"
        f"📧 {sender_email}\n\n"
        "Нажмите кнопку ниже — бот проверит все подключённые аккаунты, "
        "найдёт последнее письмо от отправителя в каждом и покажет список, "
        "отсортированный по дате получения."
    )

    if from_input:
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)


async def show_sender_hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender_email = context.user_data.get('activity_sender_email')
    if not sender_email:
        await update.callback_query.edit_message_text("Сначала выберите отправителя")
        return
    await show_sender_hub(update, context, sender_email, from_input=False)


def _scan_all_accounts(gmail_client, sender_email: str) -> list:
    accounts = gmail_client.get_all_accounts()
    active = [email for email, info in accounts.items() if info.status == "active"]
    results = []

    def worker(account_email: str):
        try:
            return gmail_client.get_latest_from_sender(account_email, sender_email)
        except Exception as e:
            print(f"Error scanning {account_email}: {e}")
            return None

    if not active:
        return results

    with ThreadPoolExecutor(max_workers=min(ACTIVITY_SCAN_WORKERS, len(active))) as pool:
        futures = [pool.submit(worker, email) for email in active]
        for fut in as_completed(futures):
            item = fut.result()
            if item:
                results.append(item)

    results.sort(key=lambda x: x.get("internal_date", 0), reverse=True)
    return results


async def scan_all_accounts_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gmail_client = context.bot_data.get('gmail_client')
    sender_email = context.user_data.get('activity_sender_email')

    if not gmail_client or not sender_email:
        await update.callback_query.edit_message_text(
            "Сначала выберите отправителя в меню «Активность»"
        )
        return

    await update.callback_query.edit_message_text(
        f"⏳ Сканирую все аккаунты по {sender_email}…"
    )

    results = await asyncio.to_thread(_scan_all_accounts, gmail_client, sender_email)

    context.user_data['activity_scan_results'] = [
        {
            "account_email": r["account_email"],
            "message_id": r["message_id"],
            "internal_date": r["internal_date"],
            "subject": extract_subject(r["message"]),
            "date": extract_date(r["message"]),
        }
        for r in results
    ]

    await render_activity_scan_page(update, context, page=0)


async def show_activity_scan_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str,
) -> None:
    try:
        page = int(callback_data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    await render_activity_scan_page(update, context, page)


async def render_activity_scan_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
) -> None:
    gmail_client = context.bot_data.get('gmail_client')
    sender_email = context.user_data.get('activity_sender_email', "")
    results = context.user_data.get('activity_scan_results') or []

    if not results:
        keyboard = [
            [InlineKeyboardButton("🔙 К отправителю", callback_data="acts")],
            [InlineKeyboardButton("🔙 К активности", callback_data="activity_menu")],
        ]
        await update.callback_query.edit_message_text(
            f"📊 По всем почтам: {sender_email}\n\n"
            "Писем от этого отправителя не найдено ни в одном аккаунте.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    total_pages = (len(results) + ACTIVITY_PAGE_SIZE - 1) // ACTIVITY_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * ACTIVITY_PAGE_SIZE
    end = min(start + ACTIVITY_PAGE_SIZE, len(results))
    page_items = results[start:end]

    lines = [
        f"📊 По всем почтам: {sender_email}",
        f"Найдено аккаунтов с письмами: {len(results)}",
        f"Страница {page + 1}/{total_pages}",
        "",
        "Отсортировано по дате получения (новые сверху):",
        "",
    ]
    keyboard = []

    for i, item in enumerate(page_items, start=start + 1):
        account = item["account_email"]
        subject = item.get("subject") or "(Без темы)"
        ts_label = _format_ts(item.get("internal_date", 0))
        lines.append(f"{i}. {account}")
        lines.append(f"   📅 {ts_label}")
        lines.append(f"   📋 {truncate_text(subject, 60)}")
        lines.append("")

        account_id = gmail_client.get_account_id(account) if gmail_client else None
        if account_id:
            keyboard.append([
                InlineKeyboardButton(
                    truncate_text(f"{i}. {account.split('@')[0]} — {subject}", 40),
                    callback_data=f"sfm:{account_id}:{item['message_id']}:asl"
                )
            ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"actpg:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"actpg:{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="actscan")])
    keyboard.append([InlineKeyboardButton("🔙 К отправителю", callback_data="acts")])
    keyboard.append([InlineKeyboardButton("🔙 К активности", callback_data="activity_menu")])

    text = "\n".join(lines).strip()
    if len(text) > 3900:
        text = text[:3900] + "\n…"

    await update.callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_full_message(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    gmail_client = context.bot_data.get('gmail_client')
    if not gmail_client:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации Gmail клиента")
        return

    if update.callback_query:
        pass  # answered in main

    try:
        parts = callback_data.split(":")
        account_id = parts[1]
        message_id = parts[2]
        back_code = parts[3] if len(parts) > 3 else "am"
    except (IndexError, ValueError):
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка разбора данных")
        return

    back_map = {
        "am": "activity_menu",
        "mm": "main_menu",
        "asl": "actpg:0",
        "activity_menu": "activity_menu",
        "main_menu": "main_menu",
    }
    back_cb = back_map.get(back_code, "activity_menu")

    email = gmail_client.resolve_email(account_id)
    if not email:
        if update.callback_query:
            await update.callback_query.edit_message_text("Аккаунт не найден")
        return

    result = gmail_client.get_message(email, message_id)

    if "error" in result:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=back_cb)]]
        await update.callback_query.edit_message_text(
            f"❌ Ошибка: {result['error']}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    sender_name, sender_email = extract_sender_info(result)
    subject = extract_subject(result)
    date = extract_date(result)
    body = extract_text_from_message(result)

    MAX_LENGTH = 4096
    header = f"📧 От: {sender_name} ({sender_email})\n📋 Тема: {subject}\n📅 Дата: {date}\n\n"

    if len(header) + len(body) > MAX_LENGTH:
        available_space = MAX_LENGTH - len(header) - 50
        body = body[:available_space] + "\n\n…(письмо обрезано, полный текст слишком длинный)"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=back_cb)]]
    await update.callback_query.edit_message_text(
        header + body,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
