"""Environment check and tokens.zip installation for Gmail Monitor Bot."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

CREDENTIALS_NAME = "credentials.json"
CREDENTIALS_DESKTOP_NAME = "credentials_desktop.json"
CREDENTIALS_DESKTOP_ALIASES = (CREDENTIALS_DESKTOP_NAME, "credentials_device.json")
TOKENS_DIR_NAME = "tokens"
TOKEN_PREFIX = "token_"
TOKEN_SUFFIX = ".json"


def project_root() -> Path:
    return Path(__file__).resolve().parent


def credentials_path(root: Path | None = None) -> Path:
    return (root or project_root()) / CREDENTIALS_NAME


def credentials_desktop_path(root: Path | None = None) -> Path:
    root = root or project_root()
    for name in CREDENTIALS_DESKTOP_ALIASES:
        path = root / name
        if path.is_file():
            return path
    return root / CREDENTIALS_DESKTOP_NAME


def tokens_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / TOKENS_DIR_NAME


def list_token_files(root: Path | None = None) -> List[Path]:
    directory = tokens_dir(root)
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.name.startswith(TOKEN_PREFIX) and p.name.endswith(TOKEN_SUFFIX)
    )


def environment_ready(root: Path | None = None) -> bool:
    """Ready when at least one token exists (tokens are self-contained for Gmail API)."""
    root = root or project_root()
    return len(list_token_files(root)) > 0


def environment_status(root: Path | None = None) -> dict:
    root = root or project_root()
    token_files = list_token_files(root)
    return {
        "ready": environment_ready(root),
        "has_credentials": credentials_path(root).is_file(),
        "has_credentials_desktop": credentials_desktop_path(root).is_file(),
        "token_count": len(token_files),
        "credentials_path": str(credentials_path(root)),
        "tokens_dir": str(tokens_dir(root)),
    }


def _is_token_member(name: str) -> bool:
    normalized = name.replace("\\", "/").lstrip("./")
    base = Path(normalized).name
    return base.startswith(TOKEN_PREFIX) and base.endswith(TOKEN_SUFFIX)


def _load_json_file(path: Path) -> Tuple[bool, Optional[str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            json.load(f)
        return True, None
    except json.JSONDecodeError:
        return False, f"{path.name} повреждён (невалидный JSON)"
    except OSError as e:
        return False, f"Не удалось прочитать {path.name}: {e}"


def install_tokens_zip(zip_path: str | Path, root: Path | None = None) -> Tuple[bool, str]:
    """
    Unpack tokens.zip into project layout:
      - token_*.json → tokens/ (required)
      - credentials_desktop.json / credentials_device.json → credentials_desktop.json (optional)
      - credentials.json → project root (optional)
    Accepts flat zip or nested folders (tokens/, etc.).
    """
    root = root or project_root()
    zip_path = Path(zip_path)

    if not zip_path.is_file():
        return False, "Файл архива не найден"

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            token_members = [n for n in names if not n.endswith("/") and _is_token_member(n)]

            if not token_members:
                return False, "В архиве нет файлов token_*.json"

            with tempfile.TemporaryDirectory(prefix="gmail_bot_setup_") as tmp:
                tmp_path = Path(tmp)
                zf.extractall(tmp_path)

                found_cred: Path | None = None
                found_desktop: Path | None = None
                found_tokens: List[Path] = []

                for path in tmp_path.rglob("*"):
                    if not path.is_file():
                        continue
                    name_lower = path.name.lower()
                    if name_lower == CREDENTIALS_NAME.lower():
                        found_cred = path
                    elif name_lower in {n.lower() for n in CREDENTIALS_DESKTOP_ALIASES}:
                        found_desktop = path
                    elif path.name.startswith(TOKEN_PREFIX) and path.name.endswith(TOKEN_SUFFIX):
                        found_tokens.append(path)

                if not found_tokens:
                    return False, "Не удалось извлечь token_*.json"

                if found_cred:
                    ok, err = _load_json_file(found_cred)
                    if not ok:
                        return False, err or "credentials.json повреждён"

                if found_desktop:
                    ok, err = _load_json_file(found_desktop)
                    if not ok:
                        return False, err or "credentials_desktop.json повреждён"

                dest_tokens = tokens_dir(root)
                dest_tokens.mkdir(parents=True, exist_ok=True)

                # Clear old tokens for a clean install
                for old in list_token_files(root):
                    try:
                        old.unlink()
                    except OSError as e:
                        logger.warning("Failed to remove old token %s: %s", old, e)

                installed: List[str] = []

                if found_cred:
                    shutil.copy2(found_cred, credentials_path(root))
                    installed.append(CREDENTIALS_NAME)

                if found_desktop:
                    # Always normalize legacy name to credentials_desktop.json
                    shutil.copy2(found_desktop, root / CREDENTIALS_DESKTOP_NAME)
                    installed.append(CREDENTIALS_DESKTOP_NAME)

                copied = 0
                used_names = set()
                for src in found_tokens:
                    ok, _ = _load_json_file(src)
                    if not ok:
                        logger.warning("Skipping invalid token JSON: %s", src)
                        continue

                    dest_name = src.name
                    if dest_name in used_names:
                        stem = src.stem
                        dest_name = f"{stem}_{copied}{TOKEN_SUFFIX}"
                    used_names.add(dest_name)
                    shutil.copy2(src, dest_tokens / dest_name)
                    copied += 1

                if copied == 0:
                    return False, "Все token_*.json в архиве невалидны"

                installed.append(f"{copied} token-файл(ов)")
                summary = ", ".join(installed)
                logger.info("Installed setup from zip: %s", summary)
                return True, f"Установлено: {summary}"

    except zipfile.BadZipFile:
        return False, "Файл не является корректным ZIP-архивом"
    except Exception as e:
        logger.exception("Failed to install tokens.zip")
        return False, f"Ошибка распаковки: {e}"
