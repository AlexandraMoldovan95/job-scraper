"""BYOK: API keys typed in the UI for this session, falling back to .env / the environment."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path
from typing import Literal

from dotenv import set_key, unset_key

from jobscraper.config import PROJECT_ROOT

ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"
SEARCHAPI_API_KEY = "SEARCHAPI_API_KEY"

KNOWN_KEYS = {
    ANTHROPIC_API_KEY: "Anthropic (Claude)",
    SEARCHAPI_API_KEY: "SearchApi.io",
}

KeySource = Literal["session", "env"]

_SESSION_PREFIX = "byok:"


class KeyStore:
    """Session keys win over .env. Keys are never logged or put in URLs.

    `session` is st.session_state in the UI (memory only, gone when the tab closes)
    and a plain dict in the CLI and in tests.
    """

    def __init__(
        self,
        session: MutableMapping[str, object] | None = None,
        *,
        env_path: Path = PROJECT_ROOT / ".env",
        environ: MutableMapping[str, str] = os.environ,
    ) -> None:
        self._session = session if session is not None else {}
        self._env_path = env_path
        self._environ = environ

    def lookup(self, name: str) -> tuple[str, KeySource] | None:
        session_value = str(self._session.get(_SESSION_PREFIX + name) or "").strip()
        if session_value:
            return session_value, "session"
        env_value = self._environ.get(name, "").strip()
        if env_value:
            return env_value, "env"
        return None

    def get(self, name: str) -> str | None:
        found = self.lookup(name)
        return found[0] if found else None

    def set_for_session(self, name: str, value: str) -> None:
        value = value.strip()
        if value:
            self._session[_SESSION_PREFIX + name] = value
        else:
            self.forget_session(name)

    def forget_session(self, name: str) -> None:
        self._session.pop(_SESSION_PREFIX + name, None)

    def save_to_env(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("empty key")
        self._env_path.touch(exist_ok=True)
        set_key(self._env_path, name, value, encoding="utf-8")
        self._environ[name] = value
        self.forget_session(name)

    def remove_from_env(self, name: str) -> None:
        if self._env_path.exists():
            unset_key(self._env_path, name, encoding="utf-8")
        self._environ.pop(name, None)


def mask(value: str) -> str:
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"
