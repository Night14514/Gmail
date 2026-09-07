import asyncio
import logging
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes
from gmail_client import GmailClient
from storage import Storage
from handlers.accounts import (
    extract_sender_info,
    extract_subject,
    extract_date,
    extract_text_from_message,
    truncate_text,
)

logger = logging.getLogger(__name__)

# 10s: ~half the old latency, safe for ~100 accounts (history.list is cheap).
# Override with CHECK_INTERVAL env (seconds, clamped 5–60).
def _resolve_check_interval() -> int:
    raw = os.environ.get("CHECK_INTERVAL", "10").strip()
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid CHECK_INTERVAL=%r, using 10", raw)
        return 10
    return max(5, min(60, value))


CHECK_INTERVAL = _resolve_check_interval()


def _sender_email_from_message(message: dict) -> str:
    _, email = extract_sender_info(message)
    return (email or "").lower().strip()


async def _notify_trigger_hit(
    bot,
    notify_chat_id: str,
    trigger,
    account_email: str,
    account_id: str,
    msg_id: str,
    message: dict,
) -> None:
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
        [
            InlineKeyboardButton(
                "Показать письмо целиком",
                callback_data=f"sfm:{account_id}:{msg_id}:mm",
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await bot.send_message(
            chat_id=notify_chat_id,
            text=notification_text,
            reply_markup=reply_markup,
        )
        logger.info(
            "Sent notification for trigger %s on account %s",
            trigger.id,
            account_email,
        )
    except Exception as e:
        logger.error("Failed to send notification: %s", e)


async def _check_triggers_via_search(
    bot,
    gmail_client: GmailClient,
    storage: Storage,
    account_email: str,
    account_id: str,
    triggers,
    notify_chat_id: str,
) -> None:
    """Fallback when historyId is missing/expired: search per trigger."""
    for trigger in triggers:
        try:
            result = await asyncio.to_thread(
                gmail_client.search_messages,
                account_email,
                f"from:{trigger.sender_email}",
                5,
            )
            if "error" in result:
                continue
            messages = result.get("messages") or []
            if not messages:
                continue
            last_seen = storage.get_last_seen_message(account_email, trigger.id)
            # Process newest-first until last_seen
            new_ids = []
            for msg in messages:
                if msg["id"] == last_seen:
                    break
                new_ids.append(msg["id"])
            for msg_id in reversed(new_ids):
                message = await asyncio.to_thread(
                    gmail_client.get_message, account_email, msg_id
                )
                if "error" in message:
                    continue
                await _notify_trigger_hit(
                    bot,
                    notify_chat_id,
                    trigger,
                    account_email,
                    account_id,
                    msg_id,
                    message,
                )
            if messages:
                storage.update_trigger_state(
                    account_email, trigger.id, messages[0]["id"]
                )
        except Exception as e:
            logger.error(
                "Search fallback failed for %s/%s: %s",
                account_email,
                trigger.id,
                e,
            )


async def run_trigger_check(bot, gmail_client: GmailClient, storage: Storage) -> None:
    """Check triggers using Gmail History API for near-instant detection."""
    triggers = storage.get_triggers()
    if not triggers:
        return

    notify_chat_id = storage.resolve_notify_chat_id()
    if not notify_chat_id:
        logger.warning("No notify_chat_id configured, skipping trigger notifications")
        return

    trigger_by_email = {
        t.sender_email.lower().strip(): t for t in triggers if t.sender_email
    }
    accounts = gmail_client.get_all_accounts()

    for account_email, account_info in accounts.items():
        if account_info.status != "active":
            continue

        account_id = account_info.account_id or gmail_client.get_account_id(account_email)
        history_id = storage.get_history_id(account_email)

        if not history_id:
            current = await asyncio.to_thread(
                gmail_client.get_profile_history_id, account_email
            )
            if current:
                storage.set_history_id(account_email, current)
            # Seed last_seen without notifying
            for trigger in triggers:
                try:
                    result = await asyncio.to_thread(
                        gmail_client.search_messages,
                        account_email,
                        f"from:{trigger.sender_email}",
                        1,
                    )
                    messages = result.get("messages") or []
                    if messages:
                        storage.update_trigger_state(
                            account_email, trigger.id, messages[0]["id"]
                        )
                except Exception as e:
                    logger.error(
                        "Bootstrap trigger state failed for %s/%s: %s",
                        account_email,
                        trigger.id,
                        e,
                    )
            continue

        hist = await asyncio.to_thread(
            gmail_client.list_history_message_ids, account_email, history_id
        )
        if hist.get("error") == "history_expired":
            logger.warning(
                "historyId expired for %s — search fallback then reset", account_email
            )
            await _check_triggers_via_search(
                bot,
                gmail_client,
                storage,
                account_email,
                account_id,
                triggers,
                notify_chat_id,
            )
            current = await asyncio.to_thread(
                gmail_client.get_profile_history_id, account_email
            )
            if current:
                storage.set_history_id(account_email, current)
            continue
        if "error" in hist:
            logger.error("History error for %s: %s", account_email, hist["error"])
            continue

        new_history_id = hist.get("history_id") or history_id
        message_ids = hist.get("message_ids") or []

        for msg_id in message_ids:
            message = await asyncio.to_thread(
                gmail_client.get_message, account_email, msg_id
            )
            if "error" in message:
                continue

            sender = _sender_email_from_message(message)
            trigger = trigger_by_email.get(sender)
            if not trigger:
                continue

            last_seen = storage.get_last_seen_message(account_email, trigger.id)
            if msg_id == last_seen:
                continue

            await _notify_trigger_hit(
                bot, notify_chat_id, trigger, account_email, account_id, msg_id, message
            )
            storage.update_trigger_state(account_email, trigger.id, msg_id)

        if new_history_id != history_id:
            storage.set_history_id(account_email, new_history_id)

    logger.debug("History-based trigger check completed")


async def check_triggers(context: ContextTypes.DEFAULT_TYPE) -> None:
    gmail_client = context.job.data.get("gmail_client")
    storage = context.job.data.get("storage")

    if not gmail_client or not storage:
        logger.error("Gmail client or storage not available in job context")
        return

    await run_trigger_check(context.bot, gmail_client, storage)


async def trigger_check_loop(
    gmail_client: GmailClient, storage: Storage, application: Application
) -> None:
    logger.info("Starting trigger check loop (interval=%ss)", CHECK_INTERVAL)
    while True:
        try:
            await run_trigger_check(application.bot, gmail_client, storage)
        except Exception as e:
            logger.error("Error in trigger check loop: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)


def start_background_jobs(
    application: Application, gmail_client: GmailClient, storage: Storage
) -> None:
    if application.job_queue:
        # Avoid duplicate jobs if called twice
        for job in application.job_queue.jobs():
            if job.name == "trigger_check":
                logger.info("Trigger check job already scheduled")
                return
        application.job_queue.run_repeating(
            check_triggers,
            interval=CHECK_INTERVAL,
            first=min(5, CHECK_INTERVAL),
            name="trigger_check",
            data={"gmail_client": gmail_client, "storage": storage},
        )
        logger.info(
            "Started background trigger check job using JobQueue (every %ss)",
            CHECK_INTERVAL,
        )
    else:
        previous = application.post_init

        async def _start_loop(app: Application) -> None:
            if previous:
                await previous(app)
            asyncio.create_task(trigger_check_loop(gmail_client, storage, app))
            logger.info("Started background trigger check task using asyncio")

        application.post_init = _start_loop
        logger.info(
            "Scheduled background trigger check via chained post_init (no JobQueue)"
        )
