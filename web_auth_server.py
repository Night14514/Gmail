"""aiohttp OAuth callback server for web-based Gmail account linking."""
from __future__ import annotations

import logging
import os
import secrets
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from aiohttp import web
from google_auth_oauthlib.flow import Flow
from telegram import InputFile

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_WEB_NAME = "credentials_web.json"

# state -> entry dict
pending_states: dict[str, dict[str, Any]] = {}

STATE_TTL_CONTRIBUTOR = 60 * 60 * 2  # 2 hours


def project_root() -> Path:
    return Path(__file__).resolve().parent


def credentials_web_path() -> Path:
    return project_root() / CREDENTIALS_WEB_NAME


def credentials_web_exists() -> bool:
    return credentials_web_path().is_file()


def register_state(
    chat_id: str,
    role: str,
    *,
    expected_email: Optional[str] = None,
    registration_id: Optional[str] = None,
) -> str:
    token = secrets.token_urlsafe(24)
    entry: dict[str, Any] = {
        "chat_id": str(chat_id),
        "role": role,
        "created_at": time.time(),
        "used": False,
        "code_verifier": None,
    }
    if role == "user_add":
        entry["expires_at"] = time.time() + STATE_TTL_CONTRIBUTOR
    if expected_email:
        entry["expected_email"] = expected_email.lower().strip()
    if registration_id:
        entry["registration_id"] = registration_id
    pending_states[token] = entry
    return token


def _redirect_uri() -> str:
    domain = os.environ.get("NGROK_STATIC_DOMAIN", "").strip()
    if not domain:
        raise RuntimeError("NGROK_STATIC_DOMAIN is not set")
    domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
    return f"https://{domain}/oauth2callback"


def _get_flow(*, code_verifier: Optional[str] = None) -> Flow:
    path = str(credentials_web_path())
    if code_verifier:
        return Flow.from_client_secrets_file(
            path,
            scopes=SCOPES,
            redirect_uri=_redirect_uri(),
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
    return Flow.from_client_secrets_file(
        path,
        scopes=SCOPES,
        redirect_uri=_redirect_uri(),
    )


async def handle_authorize(request: web.Request) -> web.Response:
    state_token = request.query.get("state")
    entry = pending_states.get(state_token) if state_token else None
    if not entry:
        return web.Response(text="Ссылка недействительна.", status=400)
    if entry.get("expires_at") and time.time() > entry["expires_at"]:
        return web.Response(text="Ссылка истекла.", status=400)
    if entry.get("used"):
        return web.Response(text="Ссылка уже использована.", status=400)

    if not credentials_web_exists():
        return web.Response(
            text="На сервере отсутствует credentials_web.json.",
            status=500,
        )

    try:
        flow = _get_flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state_token,
        )
        # Persist PKCE verifier for the callback (new Flow instance there)
        entry["code_verifier"] = flow.code_verifier
    except Exception as e:
        logger.exception("Failed to build authorization URL")
        return web.Response(text=f"Ошибка конфигурации OAuth: {e}", status=500)

    raise web.HTTPFound(auth_url)


async def handle_callback(request: web.Request) -> web.Response:
    app = request.app
    gmail_client = app["gmail_client"]
    storage = app["storage"]
    bot = app["bot"]

    state_token = request.query.get("state")
    entry = pending_states.get(state_token) if state_token else None
    if not entry or entry.get("used"):
        return web.Response(
            text="Ссылка недействительна или уже использована.",
            status=400,
        )
    if entry.get("expires_at") and time.time() > entry["expires_at"]:
        return web.Response(text="Ссылка истекла.", status=400)

    code_verifier = entry.get("code_verifier")
    if not code_verifier:
        return web.Response(
            text="Сессия авторизации повреждена (нет PKCE). Запросите новую ссылку.",
            status=400,
        )

    callback_url = str(request.url).replace("http://", "https://", 1)

    try:
        flow = _get_flow(code_verifier=code_verifier)
        flow.fetch_token(authorization_response=callback_url)
    except Exception as e:
        logger.exception("OAuth fetch_token failed")
        return web.Response(text=f"Ошибка авторизации: {e}", status=400)

    creds = flow.credentials
    raw_json = creds.to_json().encode("utf-8")

    # Peek email before persisting, to enforce expected_email
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        oauth_email = (profile.get("emailAddress") or "").lower().strip()
    except Exception as e:
        logger.exception("Failed to read OAuth profile")
        return web.Response(text=f"Не удалось получить email аккаунта: {e}", status=400)

    expected = (entry.get("expected_email") or "").lower().strip()
    if expected and oauth_email != expected:
        try:
            await bot.send_message(
                entry["chat_id"],
                f"❌ Вы авторизовались как {oauth_email}, а в заявке указана "
                f"{expected}. Войдите именно в указанный аккаунт и откройте ссылку снова "
                f"(запросите новую у владельца / через /start).",
            )
        except Exception:
            logger.exception("Failed to notify email mismatch")
        # Keep state reusable so user can retry with the correct account
        return web.Response(
            text=(
                f"Неверный Google-аккаунт: ожидался {expected}, получен {oauth_email}. "
                "Закройте вкладку и авторизуйтесь правильным аккаунтом по новой ссылке."
            ),
            status=400,
        )

    ok, message, email = gmail_client.add_account_from_json_bytes(raw_json)
    if not ok:
        try:
            await bot.send_message(
                entry["chat_id"],
                f"❌ Не удалось подключить почту: {message}",
            )
        except Exception:
            logger.exception("Failed to notify about failed account add")
        return web.Response(
            text="Не удалось подключить почту, подробности отправлены в Telegram.",
            status=400,
        )

    entry["used"] = True

    reg_id = entry.get("registration_id")
    if reg_id:
        try:
            storage.update_registration_status(reg_id, "completed")
        except Exception:
            logger.exception("Failed to mark registration completed")

    on_account_added = app.get("on_account_added")
    if on_account_added:
        try:
            result = on_account_added(email)
            if hasattr(result, "__await__"):
                await result
        except Exception:
            logger.exception("on_account_added callback failed")

    owner_chat_id = storage.resolve_notify_chat_id() if hasattr(storage, "resolve_notify_chat_id") else storage.get_notify_chat_id()
    filename = f"token_{email}.json"

    try:
        if entry["role"] == "owner_self_add":
            await bot.send_message(
                entry["chat_id"],
                f"✅ Почта {email} подключена ({message}).",
            )
            await bot.send_document(
                entry["chat_id"],
                document=InputFile(BytesIO(raw_json), filename=filename),
                caption="Резервная копия конфигурации — сохраните на случай сбоя сервера.",
            )
        else:
            await bot.send_message(
                entry["chat_id"],
                "✅ Почта успешно подключена! Введите /new, чтобы добавить ещё одну.",
            )
            if owner_chat_id:
                await bot.send_message(
                    owner_chat_id,
                    f"✅ Пользователь подключил почту {email}.",
                )
                await bot.send_document(
                    owner_chat_id,
                    document=InputFile(BytesIO(raw_json), filename=filename),
                    caption=f"Резервная копия — почта добавлена пользователем ({email}).",
                )
    except Exception:
        logger.exception("Failed to send Telegram notifications after OAuth")

    return web.Response(text="Готово! Можете закрыть эту вкладку.")


def build_app(gmail_client, storage, bot, on_account_added=None) -> web.Application:
    app = web.Application()
    app["gmail_client"] = gmail_client
    app["storage"] = storage
    app["bot"] = bot
    app["on_account_added"] = on_account_added
    app.router.add_get("/authorize", handle_authorize)
    app.router.add_get("/oauth2callback", handle_callback)
    return app


async def start_web_server(
    gmail_client, storage, bot, port: int = 5000, on_account_added=None
) -> web.AppRunner:
    app = build_app(gmail_client, storage, bot, on_account_added=on_account_added)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("OAuth callback web server started on port %s", port)
    return runner
