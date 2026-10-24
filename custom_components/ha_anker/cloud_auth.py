"""Cloud account persistence and authentication lifecycle.

The APK's encrypted login transport is not implemented here yet.  Keeping the
transport behind a protocol lets the Home Assistant flow be tested without
inventing request encryption or storing a user's password.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

STORE_KEY = "ha_anker.cloud_accounts"
STORE_VERSION = 1
REFRESH_SKEW = timedelta(minutes=2)


class CloudAuthError(Exception):
    """Base class for cloud authentication failures."""


class CloudAuthenticationError(CloudAuthError):
    """The account credentials or cached session are no longer valid."""


class CloudProtocolUnavailableError(CloudAuthError):
    """The encrypted Anker HTTP transport has not been verified yet."""


class CloudTransport(Protocol):
    """Operations required by the account and device-selection flow."""

    async def async_login(self, email: str, password: str) -> CloudAccount:
        """Authenticate and return the session material supplied by Anker."""

    async def async_refresh(self, account: CloudAccount) -> CloudAccount:
        """Refresh a cached account session."""

    async def async_list_devices(self, account: CloudAccount) -> Sequence[CloudDevice]:
        """Return devices visible to the account."""


@dataclass(frozen=True, slots=True)
class CloudAccount:
    """Persistable account session returned by the Anker cloud."""

    account_id: str
    email: str
    auth_token: str
    token_expires_at: datetime | None = None
    token_id: str | None = None
    user_id: str | None = None
    server_secret_info: str | None = None
    public_key: str | None = None

    @property
    def expires_soon(self) -> bool:
        """Return whether a refresh should happen before the next request."""
        return self.token_expires_at is not None and self.token_expires_at <= (
            datetime.now(UTC) + REFRESH_SKEW
        )

    def as_storage(self) -> dict[str, object]:
        """Serialize without ever including a password."""
        return {
            "account_id": self.account_id,
            "email": self.email,
            "auth_token": self.auth_token,
            "token_expires_at": (
                self.token_expires_at.isoformat() if self.token_expires_at else None
            ),
            "token_id": self.token_id,
            "user_id": self.user_id,
            "server_secret_info": self.server_secret_info,
            "public_key": self.public_key,
        }

    @classmethod
    def from_storage(cls, value: object) -> CloudAccount:
        """Parse one stored account and reject malformed cache entries."""
        if not isinstance(value, dict):
            raise ValueError("cloud account must be an object")
        account_id = value.get("account_id")
        email = value.get("email")
        auth_token = value.get("auth_token")
        if not all(isinstance(item, str) and item for item in (account_id, email, auth_token)):
            raise ValueError("cloud account is missing required fields")
        expires = value.get("token_expires_at")
        expires_at = datetime.fromisoformat(expires) if isinstance(expires, str) else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return cls(
            account_id=account_id,
            email=email,
            auth_token=auth_token,
            token_expires_at=expires_at,
            token_id=value.get("token_id") if isinstance(value.get("token_id"), str) else None,
            user_id=value.get("user_id") if isinstance(value.get("user_id"), str) else None,
            server_secret_info=(
                value.get("server_secret_info")
                if isinstance(value.get("server_secret_info"), str)
                else None
            ),
            public_key=(
                value.get("public_key") if isinstance(value.get("public_key"), str) else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CloudDevice:
    """Device returned by the account's device list."""

    device_id: str
    name: str
    device_sn: str | None = None
    device_pn: str | None = None
    online: bool | None = None


class UnavailableCloudTransport:
    """Safe default until the APK's encrypted HTTP exchange is recovered."""

    async def async_login(self, _email: str, _password: str) -> CloudAccount:
        raise CloudProtocolUnavailableError(
            "Anker encrypted cloud login is not implemented; no credentials were sent"
        )

    async def async_refresh(self, _account: CloudAccount) -> CloudAccount:
        raise CloudProtocolUnavailableError(
            "Anker encrypted cloud refresh is not implemented; cached credentials were kept"
        )

    async def async_list_devices(self, _account: CloudAccount) -> Sequence[CloudDevice]:
        raise CloudProtocolUnavailableError(
            "Anker encrypted device listing is not implemented; no cloud request was sent"
        )


class CloudAccountStore:
    """Persist account sessions in HA storage, keyed by account ID."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, object]] = Store(hass, STORE_VERSION, STORE_KEY)

    async def async_load(self) -> list[CloudAccount]:
        """Load valid cached sessions and discard malformed records."""
        raw = await self._store.async_load() or {}
        values = raw.get("accounts", []) if isinstance(raw, dict) else []
        accounts: list[CloudAccount] = []
        if isinstance(values, list):
            for value in values:
                try:
                    accounts.append(CloudAccount.from_storage(value))
                except TypeError, ValueError:
                    continue
        return accounts

    async def async_save(self, accounts: list[CloudAccount]) -> None:
        """Replace the cache with session material only."""
        await self._store.async_save({"accounts": [account.as_storage() for account in accounts]})

    async def async_upsert(self, account: CloudAccount) -> None:
        """Insert or replace an account session."""
        accounts = [
            item for item in await self.async_load() if item.account_id != account.account_id
        ]
        accounts.append(account)
        await self.async_save(accounts)

    async def async_remove(self, account_id: str) -> None:
        """Remove one invalid or user-deleted account."""
        await self.async_save(
            [account for account in await self.async_load() if account.account_id != account_id]
        )


class CloudAuthManager:
    """Coordinate login, refresh and account invalidation."""

    def __init__(
        self,
        hass: HomeAssistant,
        transport: CloudTransport | None = None,
        store: CloudAccountStore | None = None,
    ) -> None:
        self.store = store or CloudAccountStore(hass)
        self.transport = transport or UnavailableCloudTransport()

    async def async_login(self, email: str, password: str) -> CloudAccount:
        """Log in without retaining the submitted password."""
        if not email.strip() or not password:
            raise CloudAuthenticationError("email and password are required")
        account = await self.transport.async_login(email.strip(), password)
        await self.store.async_upsert(account)
        return account

    async def async_ensure_valid(self, account: CloudAccount) -> CloudAccount:
        """Refresh an expiring session and delete it only on auth failure."""
        if not account.expires_soon:
            return account
        try:
            refreshed = await self.transport.async_refresh(account)
        except CloudAuthenticationError:
            await self.store.async_remove(account.account_id)
            raise
        await self.store.async_upsert(refreshed)
        return refreshed

    async def async_list_devices(self, account: CloudAccount) -> Sequence[CloudDevice]:
        """Return the account's devices after refreshing when needed."""
        valid = await self.async_ensure_valid(account)
        try:
            return await self.transport.async_list_devices(valid)
        except CloudAuthenticationError:
            await self.store.async_remove(valid.account_id)
            raise

    async def async_refresh_expiring_accounts(self) -> list[CloudAccount]:
        """Refresh all expiring cached sessions for a periodic HA task."""
        refreshed: list[CloudAccount] = []
        for account in await self.store.async_load():
            if not account.expires_soon:
                refreshed.append(account)
                continue
            try:
                refreshed.append(await self.async_ensure_valid(account))
            except CloudAuthenticationError:
                continue
            except CloudProtocolUnavailableError:
                refreshed.append(account)
        return refreshed
