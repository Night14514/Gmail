import base64
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from typing import List, Optional

ACCOUNTS_PER_PAGE = 5
MESSAGES_PER_PAGE = 5

def get_status_emoji(status: str) -> str:
    if status == "active":
        return "🟢"
    elif status == "error":
        return "🔴"
    else:
        return "⚪"

def decode_message_body(data: str) -> str:
    try:
        decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        return decoded
    except Exception:
        return ""

def extract_text_from_message(message: dict) -> str:
    payload = message.get('payload', {})
    parts = payload.get('parts', [])
    
    def find_text_part(parts: List[dict], prefer_plain: bool = True) -> Optional[str]:
        for part in parts:
            mime_type = part.get('mimeType', '')
            body_data = part.get('body', {}).get('data', '')
            
            if mime_type == 'text/plain' and prefer_plain:
                return decode_message_body(body_data)
            elif mime_type == 'text/html' and not prefer_plain:
                html_content = decode_message_body(body_data)
                cleaned = re.sub(r'<[^>]+>', '', html_content)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                return cleaned
            
            if part.get('parts'):
                nested = find_text_part(part['parts'], prefer_plain)
                if nested:
                    return nested
        
        return None
    
    text = find_text_part(parts, prefer_plain=True)
    if not text:
        text = find_text_part(parts, prefer_plain=False)
    
    if not text:
        body_data = payload.get('body', {}).get('data', '')
        if body_data:
            text = decode_message_body(body_data)
    
    return text or ""

def extract_sender_info(message: dict) -> tuple[str, str]:
    headers = message.get('payload', {}).get('headers', [])
    from_header = next((h for h in headers if h['name'].lower() == 'from'), None)
    
    if from_header:
        from_value = from_header['value']
        match = re.search(r'(.+?)\s*<(.+?)>', from_value)
        if match:
            name = match.group(1).strip().strip('"')
            email = match.group(2)
        else:
            name = from_value.split('@')[0]
            email = from_value
        return name, email
    
    return "Unknown", "unknown"

def extract_sender_from_metadata(from_value: str) -> str:
    if not from_value:
        return "Unknown"
    match = re.search(r'(.+?)\s*<(.+?)>', from_value)
    if match:
        return match.group(1).strip().strip('"')
    return from_value.split('@')[0] if '@' in from_value else from_value

def extract_subject(message: dict) -> str:
    headers = message.get('payload', {}).get('headers', [])
    subject_header = next((h for h in headers if h['name'].lower() == 'subject'), None)
    return subject_header['value'] if subject_header else "(Без темы)"

def extract_date(message: dict) -> str:
    headers = message.get('payload', {}).get('headers', [])
    date_header = next((h for h in headers if h['name'].lower() == 'date'), None)
    return date_header['value'] if date_header else ""

def truncate_text(text: str, max_length: int = 40) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length-1] + "…"

def format_message_button_label(msg: dict) -> str:
    sender = extract_sender_from_metadata(msg.get('from', ''))
    subject = msg.get('subject') or "(Без темы)"
    return truncate_text(f"{sender} — {subject}", 40)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            InlineKeyboardButton("Все почты", callback_data="accounts_page:0"),
            InlineKeyboardButton("Триггеры", callback_data="triggers_menu")
        ],
        [
            InlineKeyboardButton("Активность", callback_data="activity_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(
            "📧 Gmail Monitor Bot\n\nВыберите раздел:",
            reply_markup=reply_markup
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            "📧 Gmail Monitor Bot\n\nВыберите раздел:",
            reply_markup=reply_markup
        )

async def show_accounts_page(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    gmail_client = context.bot_data.get('gmail_client')
    if not gmail_client:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации Gmail клиента")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    try:
        page = int(callback_data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    
    accounts = gmail_client.get_all_accounts()
    account_list = list(accounts.values())
    
    # Top action: Device OAuth add
    keyboard = [
        [InlineKeyboardButton("➕ Добавить аккаунт", callback_data="add_account")]
    ]

    if not account_list:
        keyboard.append(
            [InlineKeyboardButton("➕ Добавить почту", callback_data="add_email_file")]
        )
        keyboard.append(
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
        )
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            "Нет подключённых аккаунтов\n\n"
            "Добавьте аккаунт по ссылке или загрузите token_*.json.",
            reply_markup=reply_markup,
        )
        return

    total_pages = (len(account_list) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ACCOUNTS_PER_PAGE
    end_idx = min(start_idx + ACCOUNTS_PER_PAGE, len(account_list))
    page_accounts = account_list[start_idx:end_idx]

    for account in page_accounts:
        status_emoji = get_status_emoji(account.status)
        account_id = account.account_id or gmail_client.get_account_id(account.email)
        keyboard.append([
            InlineKeyboardButton(
                f"{status_emoji} {account.email}",
                callback_data=f"account:{account_id}:0",
            )
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"accounts_page:{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"accounts_page:{page+1}"))

    if nav_row:
        keyboard.append(nav_row)

    # Manual token file upload (feature 2) — between nav and main menu
    keyboard.append(
        [InlineKeyboardButton("➕ Добавить почту", callback_data="add_email_file")]
    )
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        f"📧 Аккаунты (страница {page + 1}/{total_pages}):",
        reply_markup=reply_markup,
    )


async def start_add_email_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt owner to upload a token_*.json file."""
    context.user_data["waiting_for_token_json"] = True
    keyboard = [
        [InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "➕ Добавить почту\n\n"
        "Пришлите файл `token_*.json`, полученный через `auth.py` "
        "(Desktop OAuth).\n\n"
        "Файл будет сохранён в `tokens/` и аккаунт появится в списке."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="Markdown"
        )

async def show_account_messages(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    gmail_client = context.bot_data.get('gmail_client')
    if not gmail_client:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации Gmail клиента")
        return
    
    try:
        parts = callback_data.split(":")
        account_id = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 0
    except (IndexError, ValueError):
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка разбора данных")
        return
    
    email = gmail_client.resolve_email(account_id)
    if not email:
        if update.callback_query:
            await update.callback_query.edit_message_text("Аккаунт не найден")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    if not gmail_client.health_check(email):
        keyboard = [
            [
                InlineKeyboardButton("🔄 Повторить", callback_data=f"account:{account_id}:0"),
                InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            f"❌ Ошибка доступа к аккаунту {email}\nТокен недействителен или отозван",
            reply_markup=reply_markup
        )
        return
    
    result = gmail_client.get_messages_with_metadata(email, max_results=MESSAGES_PER_PAGE)
    
    if "error" in result:
        keyboard = [
            [
                InlineKeyboardButton("🔄 Повторить", callback_data=f"account:{account_id}:0"),
                InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            f"❌ Ошибка: {result['error']}",
            reply_markup=reply_markup
        )
        return
    
    messages = result.get('messages', [])
    
    if not messages:
        keyboard = [
            [InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            f"📭 {email}\n\nНет писем",
            reply_markup=reply_markup
        )
        return
    
    keyboard = []
    for msg in messages:
        msg_id = msg['id']
        keyboard.append([
            InlineKeyboardButton(
                format_message_button_label(msg),
                callback_data=f"msg:{account_id}:{msg_id}"
            )
        ])
    
    nav_row = []
    next_page_token = result.get('nextPageToken')
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"msglist:{account_id}:{page-1}"))
    if next_page_token:
        context.user_data[f'page_token_{account_id}_{page+1}'] = next_page_token
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f"msglist:{account_id}:{page+1}"))
    
    if nav_row:
        keyboard.append(nav_row)
    
    keyboard.append([InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"📧 {email}\n\nПоследние письма:",
        reply_markup=reply_markup
    )

async def show_messages_page(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    gmail_client = context.bot_data.get('gmail_client')
    if not gmail_client:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации Gmail клиента")
        return
    
    try:
        parts = callback_data.split(":")
        account_id = parts[1]
        page = int(parts[2])
    except (IndexError, ValueError):
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка разбора данных")
        return
    
    email = gmail_client.resolve_email(account_id)
    if not email:
        if update.callback_query:
            await update.callback_query.edit_message_text("Аккаунт не найден")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    page_token = context.user_data.get(f'page_token_{account_id}_{page}')
    
    result = gmail_client.get_messages_with_metadata(
        email, max_results=MESSAGES_PER_PAGE, page_token=page_token
    )
    
    if "error" in result:
        keyboard = [
            [InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            f"❌ Ошибка: {result['error']}",
            reply_markup=reply_markup
        )
        return
    
    messages = result.get('messages', [])
    
    if not messages:
        keyboard = [
            [InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            f"📭 {email}\n\nНет писем на этой странице",
            reply_markup=reply_markup
        )
        return
    
    keyboard = []
    for msg in messages:
        msg_id = msg['id']
        keyboard.append([
            InlineKeyboardButton(
                format_message_button_label(msg),
                callback_data=f"msg:{account_id}:{msg_id}"
            )
        ])
    
    nav_row = []
    next_page_token = result.get('nextPageToken')
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"msglist:{account_id}:{page-1}"))
    if next_page_token:
        context.user_data[f'page_token_{account_id}_{page+1}'] = next_page_token
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f"msglist:{account_id}:{page+1}"))
    
    if nav_row:
        keyboard.append(nav_row)
    
    keyboard.append([InlineKeyboardButton("🔙 К списку почт", callback_data="accounts_page:0")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"📧 {email}\n\nПисьма (страница {page + 1}):",
        reply_markup=reply_markup
    )

async def show_message_full(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    gmail_client = context.bot_data.get('gmail_client')
    if not gmail_client:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации Gmail клиента")
        return
    
    try:
        parts = callback_data.split(":")
        account_id = parts[1]
        message_id = parts[2]
    except (IndexError, ValueError):
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка разбора данных")
        return
    
    email = gmail_client.resolve_email(account_id)
    if not email:
        if update.callback_query:
            await update.callback_query.edit_message_text("Аккаунт не найден")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    result = gmail_client.get_message(email, message_id)
    
    if "error" in result:
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к письмам", callback_data=f"account:{account_id}:0")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            f"❌ Ошибка: {result['error']}",
            reply_markup=reply_markup
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
    
    full_text = header + body
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад к письмам", callback_data=f"btm:{account_id}:0")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        full_text,
        reply_markup=reply_markup
    )
