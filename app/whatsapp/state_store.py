import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

SESSION_TIMEOUT_SECONDS = 900


class UserSession:
    def __init__(self, phone: str):
        self.phone: str = phone
        self.state: str = "MAIN_MENU"
        self.data: Dict[str, Any] = {}
        self.last_activity: float = time.time()

    def update_state(self, new_state: str, **kwargs):
        self.state = new_state
        self.data.update(kwargs)
        self.last_activity = time.time()

    def reset(self):
        self.state = "MAIN_MENU"
        self.data = {}
        self.last_activity = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_activity) > SESSION_TIMEOUT_SECONDS


class WhatsAppStateStore:
    def __init__(self):
        self._sessions: Dict[str, UserSession] = {}

    def get_session(self, phone: str) -> UserSession:
        clean_phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
        if clean_phone not in self._sessions:
            self._sessions[clean_phone] = UserSession(clean_phone)
        else:
            session = self._sessions[clean_phone]
            if session.is_expired():
                logger.info(f"WhatsApp session for {clean_phone} expired. Resetting.")
                session.reset()
        return self._sessions[clean_phone]

    def clear_session(self, phone: str):
        clean_phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
        if clean_phone in self._sessions:
            del self._sessions[clean_phone]


state_store = WhatsAppStateStore()
