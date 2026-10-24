"""Offline account lifecycle tests; no Anker endpoint is contacted."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ha_anker.cloud_auth import (
    CloudAccount,
    CloudAccountStore,
    CloudAuthenticationError,
    CloudAuthManager,
    CloudDevice,
    CloudProtocolUnavailableError,
)


class FakeTransport:
    def __init__(self) -> None:
        self.refresh_result: CloudAccount | Exception | None = None
        self.devices = (
            CloudDevice("device-1", "Kitchen charger", "TEST-SN-1", "A2345"),
            CloudDevice("device-2", "Office power bank", "TEST-SN-2", "A110A"),
        )

    async def async_login(self, email: str, password: str) -> CloudAccount:
        assert email == "user@example.test"
        assert password == "not-persisted"
        return CloudAccount("account-1", email, "token-1", datetime.now(UTC) + timedelta(hours=1))

    async def async_refresh(self, account: CloudAccount) -> CloudAccount:
        if isinstance(self.refresh_result, Exception):
            raise self.refresh_result
        assert self.refresh_result is not None
        return self.refresh_result

    async def async_list_devices(self, account: CloudAccount):
        assert account.account_id == "account-1"
        return self.devices


@pytest.mark.asyncio
async def test_login_persists_session_without_password(hass) -> None:
    transport = FakeTransport()
    manager = CloudAuthManager(hass, transport=transport)

    account = await manager.async_login("user@example.test", "not-persisted")
    stored = await manager.store.async_load()

    assert stored == [account]
    assert "password" not in account.as_storage()


@pytest.mark.asyncio
async def test_one_account_can_list_multiple_devices(hass) -> None:
    manager = CloudAuthManager(hass, transport=FakeTransport())
    account = await manager.async_login("user@example.test", "not-persisted")

    devices = await manager.async_list_devices(account)

    assert [device.device_id for device in devices] == ["device-1", "device-2"]


@pytest.mark.asyncio
async def test_auth_refresh_failure_removes_cached_account(hass) -> None:
    transport = FakeTransport()
    manager = CloudAuthManager(hass, transport=transport)
    account = CloudAccount(
        "account-1", "user@example.test", "expired", datetime.now(UTC) - timedelta(seconds=1)
    )
    await manager.store.async_upsert(account)
    transport.refresh_result = CloudAuthenticationError("expired")

    with pytest.raises(CloudAuthenticationError):
        await manager.async_ensure_valid(account)

    assert await manager.store.async_load() == []


@pytest.mark.asyncio
async def test_transport_unavailable_keeps_cached_account(hass) -> None:
    manager = CloudAuthManager(hass, transport=FakeTransport())
    account = CloudAccount(
        "account-1", "user@example.test", "expired", datetime.now(UTC) - timedelta(seconds=1)
    )
    await manager.store.async_upsert(account)
    manager.transport.refresh_result = CloudProtocolUnavailableError("not implemented")

    with pytest.raises(CloudProtocolUnavailableError):
        await manager.async_ensure_valid(account)

    assert await manager.store.async_load() == [account]


@pytest.mark.asyncio
async def test_periodic_refresh_removes_only_auth_invalid_accounts(hass) -> None:
    transport = FakeTransport()
    manager = CloudAuthManager(hass, transport=transport)
    account = CloudAccount(
        "account-1", "user@example.test", "expired", datetime.now(UTC) - timedelta(seconds=1)
    )
    await manager.store.async_upsert(account)
    transport.refresh_result = CloudAccount(
        "account-1", "user@example.test", "refreshed", datetime.now(UTC) + timedelta(hours=1)
    )

    refreshed = await manager.async_refresh_expiring_accounts()

    assert refreshed[0].auth_token == "refreshed"
    assert (await manager.store.async_load())[0].auth_token == "refreshed"


@pytest.mark.asyncio
async def test_malformed_storage_is_ignored(hass) -> None:
    store = CloudAccountStore(hass)
    await store._store.async_save(
        {
            "accounts": [
                {"account_id": "broken"},
                {"account_id": "ok", "email": "u", "auth_token": "t"},
            ]
        }
    )

    accounts = await store.async_load()

    assert [account.account_id for account in accounts] == ["ok"]
