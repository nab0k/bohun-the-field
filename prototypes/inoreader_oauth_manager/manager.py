"""Offline prototype of the Inoreader OAuth token manager.

No database connection, network calls, environment secrets or production integration.
The interfaces deliberately separate token storage, exclusive locking and OAuth I/O.
"""
from dataclasses import dataclass
from threading import Lock
from typing import Protocol


class RefreshFailed(RuntimeError):
    pass


class TokenPersistenceFailed(RuntimeError):
    pass


class RecoveryRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenState:
    refresh_token: str
    generation: int


@dataclass(frozen=True)
class OAuthResult:
    access_token: str
    refresh_token: str | None = None


class TokenStore(Protocol):
    def read(self) -> TokenState: ...
    def replace(self, expected_generation: int, new_refresh_token: str) -> None: ...
    def mark_uncertain(self) -> None: ...
    def is_uncertain(self) -> bool: ...


class OAuthClient(Protocol):
    def refresh(self, refresh_token: str) -> OAuthResult: ...


class TokenManager:
    """Serializes refreshes within one process; distributed lock is future work."""

    def __init__(self, store: TokenStore, client: OAuthClient, lock: Lock):
        self.store = store
        self.client = client
        self.lock = lock

    def refresh(self) -> str:
        with self.lock:
            if self.store.is_uncertain():
                raise RecoveryRequired("OAuth state uncertain; reauthorization required")
            state = self.store.read()
            try:
                result = self.client.refresh(state.refresh_token)
            except Exception as exc:
                # A timeout may occur after the provider rotated its token.
                self.store.mark_uncertain()
                raise RefreshFailed("OAuth request failed; state requires review") from exc
            if not result.access_token:
                self.store.mark_uncertain()
                raise RecoveryRequired("OAuth response missing access token")
            new_token = result.refresh_token or state.refresh_token
            if new_token != state.refresh_token:
                try:
                    self.store.replace(state.generation, new_token)
                except Exception as exc:
                    self.store.mark_uncertain()
                    raise TokenPersistenceFailed("Rotated token could not be persisted; reauthorize") from exc
            return result.access_token


class MemoryStore:
    """Test double only. Never use to store production credentials."""

    def __init__(self, refresh_token: str):
        self._state = TokenState(refresh_token, 0)
        self._uncertain = False

    def read(self) -> TokenState:
        return self._state

    def replace(self, expected_generation: int, new_refresh_token: str) -> None:
        if self._state.generation != expected_generation:
            raise ValueError("generation mismatch")
        self._state = TokenState(new_refresh_token, expected_generation + 1)

    def mark_uncertain(self) -> None:
        self._uncertain = True

    def is_uncertain(self) -> bool:
        return self._uncertain
