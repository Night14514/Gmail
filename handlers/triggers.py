import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

TRIGGERS_PER_PAGE = 5

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

async def show_triggers_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str = "triggers_menu"
) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации хранилища")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    page = 0
    if ":" in callback_data:
        try:
            page = int(callback_data.split(":")[1])
        except (IndexError, ValueError):
            page = 0
    
    triggers = storage.get_triggers()
    
    keyboard = [
        [InlineKeyboardButton("➕ Задать триггер", callback_data="add_trigger")]
    ]
    
    if triggers:
        total_pages = (len(triggers) + TRIGGERS_PER_PAGE - 1) // TRIGGERS_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        start_idx = page * TRIGGERS_PER_PAGE
        end_idx = min(start_idx + TRIGGERS_PER_PAGE, len(triggers))
        page_triggers = triggers[start_idx:end_idx]
        
        for trigger in page_triggers:
            keyboard.append([
                InlineKeyboardButton(
                    f"{trigger.sender_name} ({trigger.sender_email})",
                    callback_data=f"trigger:{trigger.id}"
                )
            ])
        
        nav_row = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton("⬅️ Назад", callback_data=f"triggers_menu:{page-1}")
            )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton("Вперёд ➡️", callback_data=f"triggers_menu:{page+1}")
            )
        if nav_row:
            keyboard.append(nav_row)
        
        text = f"🎯 Триггеры (страница {page + 1}/{total_pages})\n\nУправление триггерами на новые письма:"
    else:
        text = "🎯 Триггеры\n\nНет активных триггеров"
    
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(text, reply_markup=reply_markup)

async def start_add_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data['waiting_for_trigger_input'] = True
    context.user_data['editing_trigger_id'] = None
    
    if update.callback_query:
        await update.callback_query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🔙 Отмена", callback_data="triggers_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        "➕ Задать триггер\n\nВведите отправителя в формате:\nИмя • email\n\nПример: Cursor • no-reply@cursor.sh\n\nИли просто email, имя будет сгенерирован автоматически.",
        reply_markup=reply_markup
    )

async def process_trigger_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                [InlineKeyboardButton("🔙 Отмена", callback_data="triggers_menu")]
            ])
        )
        return
    
    trigger = storage.add_trigger(sender_name, sender_email)
    
    context.user_data['waiting_for_trigger_input'] = False
    
    keyboard = [
        [InlineKeyboardButton("🔙 К триггерам", callback_data="triggers_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ Триггер на *{sender_name} ({sender_email})* установлен на все аккаунты.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_trigger_details(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации хранилища")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    try:
        trigger_id = callback_data.split(":")[1]
    except IndexError:
        await update.callback_query.edit_message_text("Ошибка разбора данных")
        return
    
    triggers = storage.get_triggers()
    trigger = next((t for t in triggers if t.id == trigger_id), None)
    
    if not trigger:
        await update.callback_query.edit_message_text("Триггер не найден")
        return
    
    keyboard = [
        [
            InlineKeyboardButton("✏️ Изменить", callback_data=f"trigger_edit:{trigger_id}"),
            InlineKeyboardButton("🗑 Удалить", callback_data=f"trigger_delete:{trigger_id}")
        ],
        [InlineKeyboardButton("🔙 К триггерам", callback_data="triggers_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"🎯 Триггер\n\n"
        f"👤 Имя: {trigger.sender_name}\n"
        f"📧 Email: {trigger.sender_email}\n"
        f"📅 Создан: {trigger.created_at}",
        reply_markup=reply_markup
    )

async def start_edit_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    try:
        trigger_id = callback_data.split(":")[1]
    except IndexError:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка разбора данных")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    context.user_data['waiting_for_trigger_edit'] = True
    context.user_data['editing_trigger_id'] = trigger_id
    
    keyboard = [
        [InlineKeyboardButton("🔙 Отмена", callback_data="triggers_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        "✏️ Изменить триггер\n\nВведите новые данные отправителя в формате:\nИмя • email\n\nПример: Cursor • no-reply@cursor.sh",
        reply_markup=reply_markup
    )

async def process_trigger_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        await update.message.reply_text("Ошибка инициализации хранилища")
        return
    
    trigger_id = context.user_data.get('editing_trigger_id')
    if not trigger_id:
        await update.message.reply_text("Ошибка: не выбран триггер для редактирования")
        return
    
    user_input = update.message.text
    sender_name, sender_email = parse_sender_input(user_input)
    
    if "@" not in sender_email:
        await update.message.reply_text(
            "❌ Неверный формат email. Попробуйте ещё раз или нажмите кнопку отмены.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Отмена", callback_data="triggers_menu")]
            ])
        )
        return
    
    success = storage.update_trigger(trigger_id, sender_name, sender_email)
    
    context.user_data['waiting_for_trigger_edit'] = False
    context.user_data['editing_trigger_id'] = None
    
    if success:
        keyboard = [
            [InlineKeyboardButton("🔙 К триггерам", callback_data="triggers_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Триггер обновлён: *{sender_name} ({sender_email})*",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Ошибка при обновлении триггера")

async def confirm_delete_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    try:
        trigger_id = callback_data.split(":")[1]
    except IndexError:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка разбора данных")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    keyboard = [
        [
            InlineKeyboardButton("Да, удалить", callback_data=f"trigger_delete_confirm:{trigger_id}"),
            InlineKeyboardButton("Отмена", callback_data="triggers_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        "🗑 Удалить триггер?\n\nЭто действие нельзя отменить.",
        reply_markup=reply_markup
    )

async def delete_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> None:
    storage = context.bot_data.get('storage')
    if not storage:
        if update.callback_query:
            await update.callback_query.edit_message_text("Ошибка инициализации хранилища")
        return
    
    if update.callback_query:
        await update.callback_query.answer()
    
    try:
        trigger_id = callback_data.split(":")[1]
    except IndexError:
        await update.callback_query.edit_message_text("Ошибка разбора данных")
        return
    
    success = storage.delete_trigger(trigger_id)
    
    if success:
        keyboard = [
            [InlineKeyboardButton("🔙 К триггерам", callback_data="triggers_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "✅ Триггер удалён",
            reply_markup=reply_markup
        )
    else:
        await update.callback_query.edit_message_text("❌ Ошибка при удалении триггера")
