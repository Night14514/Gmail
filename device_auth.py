"""Google OAuth 2.0 Device Authorization Grant (RFC 8628) — no tunnel/web server."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly"
CREDENTIALS_DEVICE_NAME = "credentials_device.json"


def project_root() -> Path:
    return Path(__file__).resolve().parent


def credentials_device_path(root: Path | None = None) -> Path:
    return (root or project_root()) / CREDENTIALS_DEVICE_NAME


def credentials_device_exists(root: Path | None = None) -> bool:
    return credentials_device_path(root).is_file()


def load_device_client_creds(root: Path | None = None) -> dict[str, Any]:
    """Load client_id/client_secret from credentials_device.json (installed or web key)."""
    path = credentials_device_path(root)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "installed" in data:
        return data["installed"]
    if "web" in data:
        return data["web"]
    # Flat shape (client_id at top level)
    if "client_id" in data:
        return data
    raise KeyError(
        "credentials_device.json: ожидается ключ 'installed' (или 'web') с client_id"
    )


async def request_device_code(client_id: str) -> dict:
    """Возвращает {device_code, user_code, verification_url, expires_in, interval}."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            DEVICE_CODE_URL,
            data={"client_id": client_id, "scope": SCOPES},
        )
        resp.raise_for_status()
        return resp.json()


async def poll_for_token(
    client_id: str,
    client_secret: str,
    device_code: str,
    interval: int,
    expires_in: int,
) -> dict:
    """Опрашивает Google до успеха/отказа/истечения.

    Возвращает {"ok": True, "tokens": {...}} или
    {"ok": False, "reason": "access_denied"|"expired_token"|"unknown"}.
    """
    deadline = time.time() + expires_in
    async with httpx.AsyncClient(timeout=15) as client:
        while time.time() < deadline:
            await asyncio.sleep(interval)
            try:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
            except httpx.RequestError:
                # временная сетевая ошибка — не прерываем цикл
                continue
            data = resp.json()
            if resp.status_code == 200:
                return {"ok": True, "tokens": data}
            error = data.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error in ("access_denied", "expired_token"):
                return {"ok": False, "reason": error}
            return {
                "ok": False,
                "reason": data.get("error_description", error or "unknown"),
            }
    return {"ok": False, "reason": "expired_token"}
