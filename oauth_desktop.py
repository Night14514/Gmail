from __future__ import annotations

import base64
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# Fixed loopback URI — Desktop clients accept any path/port on 127.0.0.1
REDIRECT_URI = "http://127.0.0.1:8765"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly"
CREDENTIALS_DESKTOP_NAME = "credentials_desktop.json"
# Accept legacy filename from the (unsupported for Gmail) device-flow attempt
CREDENTIALS_LEGACY_NAMES = ("credentials_desktop.json", "credentials_device.json")


def project_root() -> Path:
    return Path(__file__).resolve().parent


def credentials_desktop_path(root: Path | None = None) -> Path:
    root = root or project_root()
    for name in CREDENTIALS_LEGACY_NAMES:
        path = root / name
        if path.is_file():
            return path
    return root / CREDENTIALS_DESKTOP_NAME


def credentials_desktop_exists(root: Path | None = None) -> bool:
    return credentials_desktop_path(root).is_file()


def load_desktop_client_creds(root: Path | None = None) -> dict[str, Any]:
    path = credentials_desktop_path(root)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "installed" in data:
        return data["installed"]
    if "web" in data:
        return data["web"]
    if "client_id" in data:
        return data
    raise KeyError(
        f"{path.name}: ожидается ключ 'installed' (Desktop app) с client_id"
    )


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def build_authorization_url(client_id: str) -> dict[str, str]:
    """Return {auth_url, state, code_verifier, redirect_uri}."""
    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = _pkce_pair()
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return {
        "auth_url": f"{AUTH_URL}?{urlencode(params)}",
        "state": state,
        "code_verifier": code_verifier,
        "redirect_uri": REDIRECT_URI,
    }


def extract_auth_code(text: str) -> str | None:
    """Extract OAuth code from pasted URL or raw code string."""
    raw = (text or "").strip().strip("`\"'")
    if not raw:
        return None

    if "://" in raw or raw.startswith("http"):
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
        if "code" in qs and qs["code"]:
            return qs["code"][0]
        # Sometimes users paste "code=XXX&scope=..."
        if raw.startswith("code=") or "&code=" in raw:
            qs2 = parse_qs(raw.lstrip("?"))
            if "code" in qs2 and qs2["code"]:
                return qs2["code"][0]
        return None

    # Raw authorization code (no URL)
    if " " in raw or "\n" in raw:
        return None
    if len(raw) < 10:
        return None
    return raw


async def exchange_code_for_tokens(
    client_id: str,
    client_secret: str,
    code: str,
    code_verifier: str,
    redirect_uri: str = REDIRECT_URI,
) -> dict:
    """Exchange auth code for tokens. Returns {"ok": True, "tokens": ...} or error."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        data = resp.json()
        if resp.status_code == 200 and "access_token" in data:
            return {"ok": True, "tokens": data}
        return {
            "ok": False,
            "reason": data.get("error_description")
            or data.get("error")
            or f"HTTP {resp.status_code}",
        }
