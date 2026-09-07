"""Manage ngrok tunnel for OAuth callback (static domain required)."""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

NGROK_LOCAL_API = "http://127.0.0.1:4040/api/tunnels"
TARGET_PORT = 5000


class NgrokError(Exception):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


def _normalize_domain(domain: str) -> str:
    domain = (domain or "").strip()
    domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
    return domain.lower()


def _url_matches_domain(public_url: str, domain: str) -> bool:
    try:
        host = (urlparse(public_url).hostname or "").lower()
    except Exception:
        return False
    want = _normalize_domain(domain)
    return host == want or host.endswith("." + want)


def get_active_tunnel_url(expected_domain: Optional[str] = None) -> Optional[str]:
    """Return https URL of an active tunnel targeting TARGET_PORT, if any."""
    try:
        resp = httpx.get(NGROK_LOCAL_API, timeout=2.0)
        resp.raise_for_status()
        for tunnel in resp.json().get("tunnels", []):
            if tunnel.get("proto") != "https":
                continue
            addr = str(tunnel.get("config", {}).get("addr", ""))
            if str(TARGET_PORT) not in addr:
                continue
            url = tunnel.get("public_url")
            if not url:
                continue
            if expected_domain and not _url_matches_domain(url, expected_domain):
                logger.warning(
                    "Ignoring ngrok tunnel %s (expected domain %s)",
                    url,
                    expected_domain,
                )
                continue
            return url
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError, ValueError):
        return None
    return None


def start_ngrok(domain: str) -> Tuple[bool, str]:
    """Start `ngrok http --domain=<domain> 5000` in background. Return (ok, url)."""
    domain = _normalize_domain(domain)
    try:
        # DEVNULL avoids PIPE buffer deadlock on long-running ngrok
        proc = subprocess.Popen(
            ["ngrok", "http", "--domain", domain, str(TARGET_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as e:
        raise NgrokError("not_installed", "ngrok не установлен на сервере") from e

    for _ in range(10):
        time.sleep(0.5)
        url = get_active_tunnel_url(expected_domain=domain)
        if url:
            # Detach stderr so the process can keep running without pipe pressure
            try:
                if proc.stderr:
                    proc.stderr.close()
            except Exception:
                pass
            return True, url
        if proc.poll() is not None:
            stderr = ""
            try:
                stderr = proc.stderr.read() if proc.stderr else ""
            except Exception:
                pass
            stderr = stderr or ""
            lower = stderr.lower()
            if "ERR_NGROK_4018" in stderr or "authtoken" in lower:
                raise NgrokError("no_authtoken", stderr)
            if "ERR_NGROK_105" in stderr:
                raise NgrokError("invalid_authtoken", stderr)
            if "ERR_NGROK_108" in stderr or "simultaneous" in lower:
                raise NgrokError("session_limit", stderr)
            if "address already in use" in lower:
                raise NgrokError("port_busy", stderr)
            raise NgrokError("unknown", stderr or "ngrok exited unexpectedly")

    # Timed out — kill orphan
    try:
        proc.terminate()
    except Exception:
        pass
    raise NgrokError("unknown", "Туннель не поднялся за отведённое время")


def add_authtoken(token: str) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ngrok", "config", "add-authtoken", token],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout or "unknown error"
        return True, "OK"
    except FileNotFoundError:
        return False, "ngrok не установлен на сервере"
    except subprocess.TimeoutExpired:
        return False, "таймаут при применении authtoken"


def ensure_tunnel(domain: str) -> Tuple[bool, str]:
    """
    Ensure ngrok tunnel is up for domain.
    Returns (True, public_url) or (False, error_kind).
    """
    domain = _normalize_domain(domain)
    if not domain:
        return False, "no_domain"

    existing = get_active_tunnel_url(expected_domain=domain)
    if existing:
        return True, existing

    try:
        ok, url = start_ngrok(domain)
        return ok, url
    except NgrokError as e:
        logger.warning("ngrok ensure_tunnel failed: %s — %s", e.kind, e)
        return False, e.kind
