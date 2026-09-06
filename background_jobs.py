import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes
from gmail_client import GmailClient
from storage import Storage
from handlers.accounts import extract_sender_info, extract_subject, extract_date, extract_text_from_message, truncate_text

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 300  # 5 minutes

async def run_trigger_check(bot, gmail_client: GmailClient, storage: Storage) -> None:
    triggers = storage.get_triggers()

    if not triggers:
        logger.info("No triggers configured, skipping check")
        return

    notify_chat_id = storage.get_notify_chat_id()
    if not notify_chat_id:
        logger.warning("No notify_chat_id configured, skipping trigger notifications")
        return

    accounts = gmail_client.get_all_accounts()

    for account_email, account_info in accounts.items():
        if account_info.status != "active":
            logger.info(f"Skipping inactive account: {account_email}")
            continue

        account_id = account_info.account_id or gmail_client.get_account_id(account_email)

        for trigger in triggers:
            try:
                query = f"from:{trigger.sender_email}"
                result = gmail_client.search_messages(account_email, query, max_results=10)

                if "error" in result:
                    logger.error(f"Error searching messages for {account_email}: {result['error']}")
                    continue

                messages = result.get('messages', [])
                if not messages:
                    continue

                last_seen_id = storage.get_last_seen_message(account_email, trigger.id)

                for msg in messages:
                    msg_id = msg['id']

                    if msg_id == last_seen_id:
                        break

                    message = gmail_client.get_message(account_email, msg_id)

                    if "error" in message:
                        logger.error(f"Error getting message {msg_id}: {message['error']}")
                        continue

                    sender_name, sender_email = extract_sender_info(message)
                    subject = extract_subject(message)
                    date = extract_date(message)
                    body = extract_text_from_message(message)
                    snippet = truncate_text(body, 300)

                    notification_text = (
                        f"🔔 Триггер сработал: {trigger.sender_name} ({trigger.sender_email})\n"
                        f"Аккаунт: {account_email}\n"
                        f"Тема: {subject}\n"
                        f"Дата: {date}\n\n"
                        f"Текст:\n{snippet}"
                    )

                    keyboard = [
                        [InlineKeyboardButton(
                            "Показать письмо целиком",
                            callback_data=f"sfm:{account_id}:{msg_id}:mm"
                        )]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    try:
                        await bot.send_message(
                            chat_id=notify_chat_id,
                            text=notification_text,
                            reply_markup=reply_markup
                        )
                        logger.info(f"Sent notification for trigger {trigger.id} on account {account_email}")
                    except Exception as e:
                        logger.error(f"Failed to send notification: {e}")

                if messages:
                    latest_id = messages[0]['id']
                    storage.update_trigger_state(account_email, trigger.id, latest_id)

            except Exception as e:
                logger.error(f"Error checking trigger {trigger.id} for account {account_email}: {e}")
                continue

    logger.info("Trigger check completed")

async def check_triggers(context: ContextTypes.DEFAULT_TYPE) -> None:
    gmail_client = context.job.data.get('gmail_client')
    storage = context.job.data.get('storage')

    if not gmail_client or not storage:
        logger.error("Gmail client or storage not available in job context")
        return

    await run_trigger_check(context.bot, gmail_client, storage)

async def trigger_check_loop(gmail_client: GmailClient, storage: Storage, application: Application) -> None:
    logger.info("Starting trigger check loop")

    while True:
        try:
            await run_trigger_check(application.bot, gmail_client, storage)
        except Exception as e:
            logger.error(f"Error in trigger check loop: {e}")

        await asyncio.sleep(CHECK_INTERVAL)

def start_background_jobs(application: Application, gmail_client: GmailClient, storage: Storage) -> None:
    if application.job_queue:
        application.job_queue.run_repeating(
            check_triggers,
            interval=CHECK_INTERVAL,
            first=10,
            name="trigger_check",
            data={"gmail_client": gmail_client, "storage": storage}
        )
        logger.info("Started background trigger check job using JobQueue")
    else:
        async def _start_loop(app: Application) -> None:
            asyncio.create_task(trigger_check_loop(gmail_client, storage, app))
            logger.info("Started background trigger check task using asyncio")

        application.post_init = _start_loop
        logger.info("Scheduled background trigger check via post_init (no JobQueue)")
