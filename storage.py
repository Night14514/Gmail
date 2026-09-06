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
    def from_dict(cls, data: Dict[str, Any]) -> 'Trigger':
        return cls(**data)

@dataclass
class KnownSender:
    sender_name: str
    sender_email: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KnownSender':
        return cls(**data)

class Storage:
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        self.triggers_file = os.path.join(data_dir, "triggers.json")
        self.trigger_state_file = os.path.join(data_dir, "trigger_state.json")
        self.known_senders_file = os.path.join(data_dir, "known_senders.json")
        self.bot_config_file = os.path.join(data_dir, "bot_config.json")

    def _read_json(self, filepath: str, default: Any) -> Any:
        if not os.path.exists(filepath):
            return default
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return default

    def _write_json(self, filepath: str, data: Any) -> None:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
            created_at=datetime.utcnow().isoformat() + "Z"
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

    def get_bot_config(self) -> Dict[str, Any]:
        return self._read_json(self.bot_config_file, {
            "allowed_user_ids": [],
            "notify_chat_id": ""
        })

    def save_bot_config(self, config: Dict[str, Any]) -> None:
        self._write_json(self.bot_config_file, config)

    def is_user_allowed(self, user_id: int) -> bool:
        config = self.get_bot_config()
        allowed_ids = config.get("allowed_user_ids", [])
        # Empty allowlist during first setup: anyone who knows the bot can configure it
        if not allowed_ids:
            return True
        return user_id in allowed_ids

    def has_allowed_users(self) -> bool:
        config = self.get_bot_config()
        return bool(config.get("allowed_user_ids"))

    def ensure_user_allowed(self, user_id: int, notify_chat_id: Optional[str] = None) -> None:
        """Add user to allowlist (and optionally set notify chat) if missing."""
        config = self.get_bot_config()
        allowed_ids = list(config.get("allowed_user_ids") or [])
        if user_id not in allowed_ids:
            allowed_ids.append(user_id)
            config["allowed_user_ids"] = allowed_ids
        if notify_chat_id and not config.get("notify_chat_id"):
            config["notify_chat_id"] = str(notify_chat_id)
        self.save_bot_config(config)

    def get_notify_chat_id(self) -> str:
        config = self.get_bot_config()
        return config.get("notify_chat_id", "")
