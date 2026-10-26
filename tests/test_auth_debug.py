"""Offline tests for the one-shot authentication debug exporter."""

import json
import stat

import pytest

from custom_components.ha_anker.cloud_auth import CloudAccount
from tools.anker_auth_debug import async_run

LOGIN_RESPONSE = {
    "code": 0,
    "data": {
        "auth_token": "fake-auth-token",
        "user_id": "fake-user",
        "token_id": "fake-token-id",
        "token_expires_at": "4102444800",
        "server_secret_info": "fake-server-secret",
        "extra": {"preserve": True},
    },
    "trace_id": "fake-trace",
}
MQTT_SESSION = {
    "endpoint_addr": "mqtts.example.test:8883",
    "certificate_pem": "fake-certificate",
    "private_key": "fake-private-key",
    "aws_root_ca1_pem": "fake-root-ca",
    "thing_name": "fake-client",
    "extra": "preserve-me",
}


class FakeTransport:
    @staticmethod
    def _encrypt_password(_password: str) -> tuple[str, str]:
        return "fake-public-key", "fake-encrypted-password"

    async def _post(self, path: str, _body: dict[str, object]) -> dict[str, object]:
        assert path == "passport/login"
        return LOGIN_RESPONSE

    async def async_get_mqtt_info(self, account: CloudAccount) -> dict[str, object]:
        assert account.auth_token == "fake-auth-token"
        return MQTT_SESSION


@pytest.mark.asyncio
async def test_debug_export_is_off_by_default(tmp_path) -> None:
    output = tmp_path / "anker-auth-debug.json"

    await async_run(
        "13500000000",
        "fake-password",
        transport=FakeTransport(),
        output_path=output,
    )

    assert not output.exists()


@pytest.mark.asyncio
async def test_debug_export_preserves_responses_and_uses0600(tmp_path) -> None:
    output = tmp_path / "anker-auth-debug.json"

    await async_run(
        "13500000000",
        "fake-password",
        debug_export=True,
        output_path=output,
        transport=FakeTransport(),
    )

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "login_response": LOGIN_RESPONSE,
        "mqtt_session": MQTT_SESSION,
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
