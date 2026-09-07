import json
import os
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import uuid

DATA_DIR = "data"


@dataclass
class Trigger:
    id: str
    sender_name: str
    sender_email: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trigger":
        return cls(**data)


@dataclass
class KnownSender:
    sender_name: str
    sender_email: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnownSender":
        return cls(**data)


class Storage:
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        self.triggers_file = os.path.join(data_dir, "triggers.json")
        self.trigger_state_file = os.path.join(data_dir, "trigger_state.json")
        self.known_senders_file = os.path.join(data_dir, "known_senders.json")
        self.bot_config_file = os.path.join(data_dir, "bot_config.json")
        self.pending_registrations_file = os.path.join(data_dir, "pending_registrations.json")
        self.history_state_file = os.path.join(data_dir, "history_state.json")

        self._migrate_bot_config_if_needed()

    def _read_json(self, filepath: str, default: Any) -> Any:
        if not os.path.exists(filepath):
            return default
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return default

    def _write_json(self, filepath: str, data: Any) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _default_bot_config(self) -> Dict[str, Any]:
        return {
            "owner_id": None,
            "notify_chat_id": "",
            "approved_contributor_ids": [],
        }

    def _migrate_bot_config_if_needed(self) -> None:
        """Migrate flat allowed_user_ids → owner_id + approved_contributor_ids."""
        if not os.path.exists(self.bot_config_file):
            return
        config = self._read_json(self.bot_config_file, {})
        if "owner_id" in config and "allowed_user_ids" not in config:
            return

        allowed = list(config.get("allowed_user_ids") or [])
        owner_id = config.get("owner_id")
        if owner_id is None and allowed:
            owner_id = allowed[0]

        contributors = list(config.get("approved_contributor_ids") or [])
        for uid in allowed:
            if uid != owner_id and uid not in contributors:
                contributors.append(uid)

        new_config = {
            "owner_id": owner_id,
            "notify_chat_id": config.get("notify_chat_id", ""),
            "approved_contributor_ids": contributors,
        }
        self._write_json(self.bot_config_file, new_config)

    # --- triggers ---

    def get_triggers(self) -> List[Trigger]:
        data = self._read_json(self.triggers_file, [])
        return [Trigger.from_dict(item) for item in data]

    def save_triggers(self, triggers: List[Trigger]) -> None:
        data = [trigger.to_dict() for trigger in triggers]
        self._write_json(self.triggers_file, data)

    def add_trigger(self, sender_name: str, sender_email: str) -> Trigger:
        triggers = self.get_triggers()
        trigger = Trigger(
            id=str(uuid.uuid4()),
            sender_name=sender_name,
            sender_email=sender_email,
            created_at=datetime.utcnow().isoformat() + "Z",
        )
        triggers.append(trigger)
        self.save_triggers(triggers)
        self.add_known_sender(sender_name, sender_email)
        return trigger

    def update_trigger(self, trigger_id: str, sender_name: str, sender_email: str) -> bool:
        triggers = self.get_triggers()
        for trigger in triggers:
            if trigger.id == trigger_id:
                trigger.sender_name = sender_name
                trigger.sender_email = sender_email
                self.save_triggers(triggers)
                self.add_known_sender(sender_name, sender_email)
                return True
        return False

    def delete_trigger(self, trigger_id: str) -> bool:
        triggers = self.get_triggers()
        original_length = len(triggers)
        triggers = [t for t in triggers if t.id != trigger_id]
        if len(triggers) < original_length:
            self.save_triggers(triggers)
            return True
        return False

    def get_trigger_state(self) -> Dict[str, Dict[str, str]]:
        return self._read_json(self.trigger_state_file, {})

    def save_trigger_state(self, state: Dict[str, Dict[str, str]]) -> None:
        self._write_json(self.trigger_state_file, state)

    def update_trigger_state(self, account_email: str, trigger_id: str, message_id: str) -> None:
        state = self.get_trigger_state()
        if account_email not in state:
            state[account_email] = {}
        state[account_email][trigger_id] = message_id
        self.save_trigger_state(state)

    def get_last_seen_message(self, account_email: str, trigger_id: str) -> str:
        state = self.get_trigger_state()
        return state.get(account_email, {}).get(trigger_id, "")

    # --- history state (Gmail History API) ---

    def get_history_state(self) -> Dict[str, str]:
        """Map account_email → last historyId (as string)."""
        return self._read_json(self.history_state_file, {})

    def get_history_id(self, account_email: str) -> Optional[str]:
        return self.get_history_state().get(account_email)

    def set_history_id(self, account_email: str, history_id: str) -> None:
        state = self.get_history_state()
        state[account_email] = str(history_id)
        self._write_json(self.history_state_file, state)

    # --- known senders ---

    def get_known_senders(self) -> List[KnownSender]:
        data = self._read_json(self.known_senders_file, [])
        return [KnownSender.from_dict(item) for item in data]

    def save_known_senders(self, senders: List[KnownSender]) -> None:
        data = [sender.to_dict() for sender in senders]
        self._write_json(self.known_senders_file, data)

    def add_known_sender(self, sender_name: str, sender_email: str) -> None:
        senders = self.get_known_senders()
        email_set = {s.sender_email for s in senders}
        if sender_email not in email_set:
            senders.append(KnownSender(sender_name=sender_name, sender_email=sender_email))
            self.save_known_senders(senders)

    # --- bot config / roles ---

    def get_bot_config(self) -> Dict[str, Any]:
        config = self._read_json(self.bot_config_file, self._default_bot_config())
        # Ensure new keys exist even if file is partial
        defaults = self._default_bot_config()
        for key, value in defaults.items():
            config.setdefault(key, value)
        return config

    def save_bot_config(self, config: Dict[str, Any]) -> None:
        self._write_json(self.bot_config_file, config)

    def get_owner_id(self) -> Optional[int]:
        owner = self.get_bot_config().get("owner_id")
        return int(owner) if owner is not None else None

    def set_owner_id_if_empty(self, user_id: int) -> bool:
        config = self.get_bot_config()
        if config.get("owner_id"):
            return False
        config["owner_id"] = user_id
        if not config.get("notify_chat_id"):
            config["notify_chat_id"] = str(user_id)
        self.save_bot_config(config)
        return True

    def is_owner(self, user_id: int) -> bool:
        return self.get_owner_id() == user_id

    def is_approved_contributor(self, user_id: int) -> bool:
        return user_id in self.get_bot_config().get("approved_contributor_ids", [])

    def ensure_user_allowed_contributor(self, user_id: int) -> None:
        config = self.get_bot_config()
        contributors = list(config.get("approved_contributor_ids") or [])
        if user_id not in contributors:
            contributors.append(user_id)
            config["approved_contributor_ids"] = contributors
            self.save_bot_config(config)

    def ensure_owner_notify_chat(self, user_id: int, chat_id: Optional[str] = None) -> None:
        """Ensure owner_id and notify_chat_id are set (used after first setup)."""
        config = self.get_bot_config()
        if not config.get("owner_id"):
            config["owner_id"] = user_id
        if chat_id and not config.get("notify_chat_id"):
            config["notify_chat_id"] = str(chat_id)
        self.save_bot_config(config)

    def get_notify_chat_id(self) -> str:
        config = self.get_bot_config()
        return config.get("notify_chat_id", "")

    # Backwards-compatible helpers used during setup zip install
    def is_user_allowed(self, user_id: int) -> bool:
        """True for owner or approved contributor (or no owner yet — bootstrap)."""
        if self.get_owner_id() is None:
            return True
        return self.is_owner(user_id) or self.is_approved_contributor(user_id)

    def ensure_user_allowed(self, user_id: int, notify_chat_id: Optional[str] = None) -> None:
        """Bootstrap owner on first successful setup."""
        self.ensure_owner_notify_chat(user_id, notify_chat_id)

    # --- pending registrations ---

    def get_registrations(self) -> List[Dict[str, Any]]:
        return self._read_json(self.pending_registrations_file, [])

    def save_registrations(self, registrations: List[Dict[str, Any]]) -> None:
        self._write_json(self.pending_registrations_file, registrations)

    def add_registration_request(self, user_id: int, chat_id: str, email: str) -> str:
        registrations = self.get_registrations()
        request_id = str(uuid.uuid4())
        registrations.append(
            {
                "id": request_id,
                "user_id": user_id,
                "chat_id": str(chat_id),
                "email": email.lower().strip(),
                "status": "pending",
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
        )
        self.save_registrations(registrations)
        return request_id

    def get_registration_by_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        registrations = self.get_registrations()
        user_regs = [r for r in registrations if r.get("user_id") == user_id]
        if not user_regs:
            return None
        return user_regs[-1]

    def get_registration(self, request_id: str) -> Optional[Dict[str, Any]]:
        for reg in self.get_registrations():
            if reg.get("id") == request_id:
                return reg
        return None

    def update_registration_status(self, request_id: str, status: str) -> None:
        registrations = self.get_registrations()
        for reg in registrations:
            if reg.get("id") == request_id:
                reg["status"] = status
                break
        self.save_registrations(registrations)

    def find_registration_by_email(
        self, email: str, statuses: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Find latest registration for email, optionally filtered by status."""
        email_norm = email.lower().strip()
        matches = [
            r
            for r in self.get_registrations()
            if r.get("email", "").lower() == email_norm
            and (statuses is None or r.get("status") in statuses)
        ]
        if not matches:
            return None
        return matches[-1]

    def is_email_registration_blocked(
        self, email: str, *, connected_emails: Optional[set] = None
    ) -> Optional[str]:
        """
        Return a reason if this email cannot be registered again.
        - pending / approved (awaiting OAuth): blocked
        - completed: blocked only if the mailbox is still connected
        """
        email_norm = email.lower().strip()
        pending = self.find_registration_by_email(email_norm, statuses=["pending"])
        if pending:
            return "На эту почту уже есть заявка на рассмотрении."

        approved = self.find_registration_by_email(email_norm, statuses=["approved"])
        if approved:
            return (
                "Эта почта уже одобрена и ожидает авторизации. "
                "Пользователь должен открыть ссылку или запросить новую через /start."
            )

        completed = self.find_registration_by_email(email_norm, statuses=["completed"])
        if completed:
            connected = connected_emails or set()
            connected_l = {e.lower().strip() for e in connected}
            if email_norm in connected_l:
                return "Эта почта уже подключена к боту."
            # completed but account removed — allow new registration
        return None

    def resolve_notify_chat_id(self) -> str:
        """notify_chat_id, falling back to owner_id as chat id."""
        chat = self.get_notify_chat_id()
        if chat:
            return str(chat)
        owner = self.get_owner_id()
        return str(owner) if owner is not None else ""
