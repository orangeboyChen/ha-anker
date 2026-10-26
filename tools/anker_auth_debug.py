"""One-shot Anker authentication debug exporter.

This module is intentionally separate from the Home Assistant integration.  It
does not write anything unless ``debug_export``/``--debug-export`` is enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from custom_components.ha_anker.cloud_auth import (
    AnkerCloudTransport,
    CloudAccount,
    CloudAuthenticationError,
)

DEFAULT_DEBUG_EXPORT_PATH = Path.home() / "Downloads" / "anker-auth-debug.json"


def write_auth_debug_export(
    path: str | Path,
    *,
    login_response: dict[str, Any],
    mqtt_session: dict[str, Any],
) -> Path:
    """Atomically write the complete fake/real response bundle with mode 0600."""
    target = Path(path).expanduser()
    payload = {"login_response": login_response, "mqtt_session": mqtt_session}
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        os.chmod(target, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise
    return target


async def async_login_with_response(
    transport: AnkerCloudTransport, phone: str, password: str
) -> tuple[CloudAccount, dict[str, Any]]:
    """Perform the login request while retaining its JSON response for export."""
    public_key, encrypted_password = transport._encrypt_password(password)
    now = datetime.now().astimezone()
    response = await transport._post(
        "passport/login",
        {
            "ab": "CN",
            "client_secret_info": {"public_key": public_key},
            "enc": 0,
            "email": phone.strip(),
            "password": encrypted_password,
            "time_zone": int(now.utcoffset().total_seconds() * 1000),
            "transaction": str(int(now.timestamp() * 1000)),
        },
    )
    data = response.get("data")
    if not isinstance(data, dict) or not data.get("auth_token") or not data.get("user_id"):
        raise CloudAuthenticationError("Anker login response did not include a valid session")
    expiry = data.get("token_expires_at")
    account = CloudAccount(
        account_id=str(data["user_id"]),
        email=phone.strip(),
        auth_token=str(data["auth_token"]),
        token_expires_at=(datetime.fromtimestamp(float(expiry), UTC) if expiry else None),
        token_id=str(data["token_id"]) if data.get("token_id") else None,
        user_id=str(data["user_id"]),
        server_secret_info=(
            str(data["server_secret_info"]) if data.get("server_secret_info") else None
        ),
        public_key=public_key,
    )
    return account, response


async def async_run(
    phone: str,
    password: str,
    *,
    debug_export: bool = False,
    output_path: str | Path = DEFAULT_DEBUG_EXPORT_PATH,
    transport: AnkerCloudTransport | None = None,
) -> tuple[CloudAccount, dict[str, Any], dict[str, Any]]:
    """Run one login/MQTT initialization; export only when explicitly enabled."""
    active_transport = transport or AnkerCloudTransport()
    try:
        account, login_response = await async_login_with_response(active_transport, phone, password)
        mqtt_session = await active_transport.async_get_mqtt_info(account)
        if debug_export:
            write_auth_debug_export(
                output_path, login_response=login_response, mqtt_session=mqtt_session
            )
        return account, login_response, mqtt_session
    finally:
        if transport is None:
            await active_transport.async_close()


def main() -> None:
    """Run the one-shot command-line tester."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--debug-export", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_DEBUG_EXPORT_PATH)
    args = parser.parse_args()
    password = getpass.getpass("Anker password: ")
    account, _login_response, _mqtt_session = asyncio.run(
        async_run(
            args.phone,
            password,
            debug_export=args.debug_export,
            output_path=args.output,
        )
    )
    print(f"Authenticated account {account.account_id}; debug export: {args.debug_export}")


if __name__ == "__main__":
    main()
