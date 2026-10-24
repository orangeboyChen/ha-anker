"""Anker cloud login, device discovery and account session lifecycle."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from aiohttp import ClientError, ClientSession, ClientTimeout
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORE_KEY = "ha_anker.cloud_accounts"
STORE_VERSION = 1
REFRESH_SKEW = timedelta(minutes=2)
API_BASE = "https://aiot-api-cn.anker.com.cn"
API_PUBLIC_KEY = bytes.fromhex(
    "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a3"
    "868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"
)
DEVICE_LIST_PATH = "power_service/v1/app/get_relate_and_bind_devices"
MQTT_INFO_PATH = "v1/openapi/devicemanage/get_user_mqtt_info"
SUPPORTED_PRODUCTS = {"A110A", "A2345"}
_LOGGER = logging.getLogger(__name__)


def _gmt_timezone() -> str:
    offset = datetime.now().astimezone().strftime("%z")
    return f"GMT{offset[:3]}:{offset[3:]}"


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
    transport: str = "cloud"
    address: str | None = None


class AnkerCloudTransport:
    """Implement the verified Anker Power HTTPS and MQTT-info endpoints."""

    def __init__(self) -> None:
        self._session: ClientSession | None = None

    async def _client(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=20))
        return self._session

    async def async_close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post(
        self,
        path: str,
        body: dict[str, object],
        *,
        account: CloudAccount | None = None,
    ) -> dict[str, object]:
        headers = {
            "content-type": "application/json",
            "model-type": "DESKTOP",
            "app-name": "anker_power",
            "os-type": "android",
            "country": "CN",
            "timezone": _gmt_timezone(),
        }
        if account:
            headers.update(
                {
                    "gtoken": hashlib.md5(account.user_id.encode()).hexdigest()
                    if account.user_id
                    else "",
                    "x-auth-token": account.auth_token,
                }
            )
        client = await self._client()
        try:
            async with client.post(f"{API_BASE}/{path}", headers=headers, json=body) as response:
                data = await response.json(content_type=None)
                if response.status in (401, 403):
                    raise CloudAuthenticationError("Anker session is no longer valid")
                if response.status >= 400 or not isinstance(data, dict):
                    raise CloudAuthError(f"Anker request failed ({response.status})")
        except ClientError as err:
            raise CloudAuthError("Unable to reach Anker cloud") from err
        code = data.get("code", 0)
        if code not in (0, "0", None):
            message = str(data.get("msg", "Anker rejected the request"))
            if str(code) in {"10001", "10002", "26085"}:
                raise CloudAuthenticationError("Anker credentials or session are invalid")
            raise CloudAuthError(message[:160])
        return data

    @staticmethod
    def _encrypt_password(password: str) -> tuple[str, str]:
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        remote_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), API_PUBLIC_KEY)
        shared_key = private_key.exchange(ec.ECDH(), remote_key)
        padder = padding.PKCS7(128).padder()
        padded = padder.update(password.encode()) + padder.finalize()
        encryptor = Cipher(algorithms.AES(shared_key), modes.CBC(shared_key[:16])).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        import base64

        return public_key.hex(), base64.b64encode(encrypted).decode()

    async def async_login(self, email: str, password: str) -> CloudAccount:
        public_key, encrypted_password = self._encrypt_password(password)
        now = datetime.now().astimezone()
        response = await self._post(
            "passport/login",
            {
                "ab": "CN",
                "client_secret_info": {"public_key": public_key},
                "enc": 0,
                "email": email,
                "password": encrypted_password,
                "time_zone": int(now.utcoffset().total_seconds() * 1000),
                "transaction": str(int(now.timestamp() * 1000)),
            },
        )
        data = response.get("data")
        if not isinstance(data, dict) or not data.get("auth_token") or not data.get("user_id"):
            raise CloudAuthenticationError("Anker login response did not include a valid session")
        expiry = data.get("token_expires_at")
        return CloudAccount(
            account_id=str(data.get("user_id")),
            email=email,
            auth_token=str(data["auth_token"]),
            token_expires_at=(datetime.fromtimestamp(float(expiry), UTC) if expiry else None),
            token_id=str(data["token_id"]) if data.get("token_id") else None,
            user_id=str(data["user_id"]),
            server_secret_info=(
                str(data["server_secret_info"]) if data.get("server_secret_info") else None
            ),
            public_key=public_key,
        )

    async def async_refresh(self, account: CloudAccount) -> CloudAccount:
        raise CloudAuthenticationError("Sign in again to refresh this Anker account")

    async def async_list_devices(self, account: CloudAccount) -> Sequence[CloudDevice]:
        response = await self._post(DEVICE_LIST_PATH, {}, account=account)
        data = response.get("data")
        if isinstance(data, dict):
            data = data.get("data", [])
        if not isinstance(data, list):
            return []
        devices: list[CloudDevice] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            product = item.get("product_code") or item.get("device_pn")
            serial = item.get("device_sn")
            if product not in SUPPORTED_PRODUCTS or not serial:
                continue
            ble_only = product == "A110A"
            devices.append(
                CloudDevice(
                    device_id=str(serial),
                    name=str(item.get("alias_name") or item.get("device_name") or product),
                    device_sn=str(serial),
                    device_pn=str(product),
                    online=bool(item.get("wifi_online")) if not ble_only else None,
                    transport="ble" if ble_only else "cloud",
                    address=(
                        str(item.get("bt_ble_mac")) if ble_only and item.get("bt_ble_mac") else None
                    ),
                )
            )
        return devices

    async def async_get_mqtt_info(self, account: CloudAccount) -> dict[str, object]:
        response = await self._post(MQTT_INFO_PATH, {}, account=account)
        data = response.get("data")
        if not isinstance(data, dict) or not all(
            isinstance(data.get(key), str) and data.get(key)
            for key in ("endpoint_addr", "certificate_pem", "private_key", "aws_root_ca1_pem")
        ):
            raise CloudAuthError("Anker did not return valid MQTT client credentials")
        return data


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
        self.transport = transport or AnkerCloudTransport()
        # Passwords are shared only in process so a coordinator created after
        # config flow completion can refresh the same account without writing
        # credentials to HA storage.  The cache disappears on restart.
        self._passwords = hass.data.setdefault(DOMAIN, {}).setdefault("cloud_passwords", {})

    def set_password(self, account_id: str, password: str) -> None:
        """Keep the submitted credential in process memory for token renewal."""
        if password:
            self._passwords[account_id] = password

    async def async_login(self, email: str, password: str) -> CloudAccount:
        """Log in without retaining the submitted password."""
        if not email.strip() or not password:
            raise CloudAuthenticationError("email and password are required")
        account = await self.transport.async_login(email.strip(), password)
        self._passwords[account.account_id] = password
        await self.store.async_upsert(account)
        return account

    async def async_ensure_valid(self, account: CloudAccount) -> CloudAccount:
        """Refresh an expiring session and delete it only on auth failure."""
        if not account.expires_soon:
            return account
        try:
            if password := self._passwords.get(account.account_id):
                refreshed = await self.transport.async_login(account.email, password)
                self._passwords[refreshed.account_id] = password
            else:
                refreshed = await self.transport.async_refresh(account)
        except CloudAuthenticationError:
            self._passwords.pop(account.account_id, None)
            await self.store.async_remove(account.account_id)
            raise
        await self.store.async_upsert(refreshed)
        return refreshed

    async def async_invalidate(self, account_id: str) -> None:
        """Remove a session and its in-memory password after auth rejection."""
        self._passwords.pop(account_id, None)
        await self.store.async_remove(account_id)

    async def async_list_devices(self, account: CloudAccount) -> Sequence[CloudDevice]:
        """Return the account's devices after refreshing when needed."""
        valid = await self.async_ensure_valid(account)
        try:
            return await self.transport.async_list_devices(valid)
        except CloudAuthenticationError:
            await self.async_invalidate(valid.account_id)
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
