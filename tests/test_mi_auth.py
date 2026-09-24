"""Unit tests for Xiaomi MiAuth cryptography and encrypted UART frames."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

MI_AUTH_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "downg_scooter"
    / "mi_auth.py"
)
MODULE_NAME = "mi_auth_test"
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, MI_AUTH_PATH)
assert SPEC is not None and SPEC.loader is not None
mi_auth = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mi_auth
SPEC.loader.exec_module(mi_auth)


class MiUartSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        key = mi_auth.EncryptionKey(
            key=bytes(range(16)),
            iv=bytes.fromhex("AA BB CC DD"),
        )
        self.session = mi_auth.MiUartSession(
            mi_auth.SessionKeys(device=key, application=key)
        )

    def test_encrypted_read_frame_matches_known_vector(self) -> None:
        with patch(
            f"{MODULE_NAME}.secrets.token_bytes",
            return_value=bytes.fromhex("11 22 33 44"),
        ):
            frame = self.session.build_frame(0x22, 0x01, 0x31, b"\x0A")

        self.assertEqual(
            frame,
            bytes.fromhex(
                "55 AB 03 00 00 F1 9F AD F7 DC 9C F4 32 03 C3 23 7E C3 F8"
            ),
        )

    def test_encrypted_frame_round_trip(self) -> None:
        with patch(
            f"{MODULE_NAME}.secrets.token_bytes",
            return_value=bytes.fromhex("11 22 33 44"),
        ):
            frame = self.session.build_frame(
                0x25,
                0x01,
                0x31,
                bytes.fromhex("31 1A 55 00 00 00 78 0F 2F 2F"),
            )

        decoded = self.session.decode_frame(frame)
        self.assertEqual(decoded.source, 0x25)
        self.assertEqual(decoded.command, 0x01)
        self.assertEqual(decoded.argument, 0x31)
        self.assertEqual(
            decoded.payload,
            bytes.fromhex("31 1A 55 00 00 00 78 0F 2F 2F"),
        )

    def test_invalid_checksum_is_rejected(self) -> None:
        with patch(
            f"{MODULE_NAME}.secrets.token_bytes",
            return_value=bytes(4),
        ):
            frame = bytearray(self.session.build_frame(0x22, 0x01, 0x31, b"\x0A"))
        frame[-1] ^= 0x01

        with self.assertRaisesRegex(mi_auth.MiAuthError, "checksum"):
            self.session.decode_frame(bytes(frame))


class MiUartClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_register_reads_share_one_encrypted_session(self) -> None:
        key = mi_auth.EncryptionKey(bytes(range(16)), bytes.fromhex("AA BB CC DD"))
        keys = mi_auth.SessionKeys(device=key, application=key)
        response_session = mi_auth.MiUartSession(keys)

        class FakeClient:
            def __init__(self) -> None:
                self.callback: Callable[[object, bytearray], None] | None = None
                self.response_index = 0

            async def stop_notify(self, _uuid: str) -> None:
                return None

            async def start_notify(
                self,
                _uuid: str,
                callback: Callable[[object, bytearray], None],
            ) -> None:
                self.callback = callback

            async def write_gatt_char(
                self, _uuid: str, _data: bytes, *, response: bool
            ) -> None:
                payloads = (bytes.fromhex("64 00"), bytes.fromhex("70 00"))
                payload = payloads[self.response_index]
                self.response_index += 1
                with patch(
                    f"{MODULE_NAME}.secrets.token_bytes",
                    return_value=bytes(4),
                ):
                    frame = response_session.build_frame(
                        0x25, 0x01, 0x31, payload
                    )
                callback = self.callback
                assert callback is not None
                callback(None, bytearray(frame))

        client = FakeClient()
        uart = mi_auth.MiUartClient(
            client,
            mi_auth.MiUartSession(keys),
        )
        await uart.start()
        try:
            first = await uart.read_register(0x22, 0x31, 2)
            second = await uart.read_register(0x22, 0x31, 2)
        finally:
            await uart.stop()

        self.assertEqual(first, bytes.fromhex("64 00"))
        self.assertEqual(second, bytes.fromhex("70 00"))


class RegistrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_key_timeout_requests_restart_without_cancel_command(
        self,
    ) -> None:
        class TimeoutTransport:
            def __init__(self) -> None:
                self.control_writes: list[bytes] = []
                self.auth_writes: list[bytes] = []

            async def write_control(self, data: bytes) -> None:
                self.control_writes.append(data)

            async def write_auth(self, data: bytes) -> None:
                self.auth_writes.append(data)

            async def read_parcel(self, _expected_command: int) -> bytes:
                return bytes(16)

            async def write_parcel(self, _data: bytes) -> None:
                raise TimeoutError

        transport = TimeoutTransport()

        with self.assertRaisesRegex(
            mi_auth.MiAuthRestartRequired,
            "No READY response",
        ):
            await mi_auth._register(transport)

        self.assertEqual(
            transport.control_writes,
            [mi_auth.GET_INFO, mi_auth.SET_KEY],
        )
        self.assertEqual(transport.auth_writes, [mi_auth.SEND_DATA])


class ParcelTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_command_bearing_parcel_header_is_accepted(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.writes: list[tuple[str, bytes]] = []

            async def write_gatt_char(
                self, uuid: str, data: bytes, *, response: bool
            ) -> None:
                self.writes.append((uuid, data))

        client = FakeClient()
        transport = mi_auth.MiAuthTransport(client)
        transport._auth_queue.put_nowait(bytes.fromhex("00 00 00 03 04 00"))
        expected = bytes(range(64))
        for index, offset in enumerate(range(0, 64, 18), start=1):
            transport._auth_queue.put_nowait(
                index.to_bytes(2, "little") + expected[offset : offset + 18]
            )

        result = await transport.read_parcel(mi_auth.PARCEL_PUBLIC_KEY)

        self.assertEqual(result, expected)
        self.assertEqual(
            [data for _uuid, data in client.writes],
            [mi_auth.READY, mi_auth.OK],
        )

    async def test_unexpected_parcel_command_is_rejected(self) -> None:
        transport = mi_auth.MiAuthTransport(object())
        transport._auth_queue.put_nowait(bytes.fromhex("00 00 00 03 04 00"))

        with self.assertRaisesRegex(mi_auth.MiAuthError, "expected 0x0D"):
            await transport.read_parcel(mi_auth.PARCEL_LOGIN_RANDOM)

    async def test_control_and_auth_notifications_stay_separate(self) -> None:
        transport = mi_auth.MiAuthTransport(object())
        transport._auth_notification(None, bytearray(mi_auth.READY))
        transport._control_notification(None, bytearray(mi_auth.AUTH_OK))

        self.assertEqual(await transport.next_auth(), mi_auth.READY)
        self.assertEqual(await transport.next_control(), mi_auth.AUTH_OK)


if __name__ == "__main__":
    unittest.main()
