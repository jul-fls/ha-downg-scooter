"""Unit tests for Xiaomi register framing and advertisement selection."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).parents[1] / "custom_components" / "downg_scooter"
PACKAGE = "downg_scooter_test"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


const = _load("const")
_load("mi_auth")
protocol = _load("protocol")
PROTOCOL_MIAUTH = const.PROTOCOL_MIAUTH
PROTOCOL_PLAIN = const.PROTOCOL_PLAIN


class XiaomiFrameTest(unittest.TestCase):
    def test_lock_state_read_frame_matches_downg(self) -> None:
        frame = protocol._build_xiaomi_frame(
            destination=0x20, command=0x01, argument=0xB2, payload=b"\x02"
        )
        self.assertEqual(frame, bytes.fromhex("55 AA 03 20 01 B2 02 27 FF"))

    def test_lock_write_frame_matches_downg(self) -> None:
        frame = protocol._build_xiaomi_frame(
            destination=0x20, command=0x03, argument=0x70, payload=b"\x01\x00"
        )
        self.assertEqual(frame, bytes.fromhex("55 AA 04 20 03 70 01 00 67 FF"))

    def test_unlock_write_frame_matches_downg(self) -> None:
        frame = protocol._build_xiaomi_frame(
            destination=0x20, command=0x03, argument=0x71, payload=b"\x01\x00"
        )
        self.assertEqual(frame, bytes.fromhex("55 AA 04 20 03 71 01 00 66 FF"))

    def test_extract_response_payload(self) -> None:
        frame = bytes.fromhex("55 AA 04 23 01 31 64 00 42 FF")
        self.assertEqual(protocol._extract_response_payload(frame, 0x01, 0x31), b"\x64\x00")

    def test_fragmented_notification_is_reassembled(self) -> None:
        client = protocol.DownGScooterClient("00:11:22:33:44:55")
        frame = bytes.fromhex("55 AA 04 23 01 31 64 00 42 FF")
        client._handle_notify(None, bytearray(frame[:4]))
        self.assertTrue(client._response_queue.empty())
        client._handle_notify(None, bytearray(frame[4:]))
        self.assertEqual(client._response_queue.get_nowait(), frame)

    def test_bms_runtime_response_from_m365_is_reassembled(self) -> None:
        client = protocol.DownGScooterClient("00:11:22:33:44:55")
        frame = bytes.fromhex("55 AA 0C 25 01 31 31 1A 55 00 00 00 78 0F 2F 2F 17 FE")
        client._handle_notify(None, bytearray(frame))
        self.assertEqual(
            protocol._extract_response_payload(frame, 0x01, 0x31),
            bytes.fromhex("31 1A 55 00 00 00 78 0F 2F 2F"),
        )

    def test_missing_notifications_are_reported(self) -> None:
        client = protocol.DownGScooterClient("00:11:22:33:44:55")
        self.assertIn("no BLE notification", client._timeout_diagnostic(0x31))

    def test_bcd_version_and_battery_date(self) -> None:
        self.assertEqual(protocol._decode_bcd_version(0x42, 0x01), "1.4.2")
        packed_date = ((2021 - 2000) << 9) | (7 << 5) | 19
        self.assertEqual(protocol._decode_battery_date(packed_date), "2021-07-19")


class AdvertisementModeTest(unittest.TestCase):
    def test_user_scooter_fe95_advertisement_selects_miauth(self) -> None:
        service_data = {
            protocol.MI_SERVICE_UUID: bytes.fromhex(
                "30 58 66 07 01 8D 13 18 A0 5D D4 08"
            )
        }
        self.assertEqual(
            protocol.protocol_mode_from_advertisement({}, service_data),
            PROTOCOL_MIAUTH,
        )

    def test_unknown_advertisement_keeps_plain_compatibility(self) -> None:
        self.assertEqual(
            protocol.protocol_mode_from_advertisement({}, {}), PROTOCOL_PLAIN
        )

    def test_connected_and_disconnected_intervals(self) -> None:
        self.assertEqual(const.CONNECTED_POLL_INTERVAL, 10)
        self.assertEqual(const.DISCONNECTED_RETRY_INTERVAL, 60)


class BmsStatusTest(unittest.TestCase):
    def test_real_charger_status_is_charging(self) -> None:
        self.assertTrue(protocol.is_bms_charging(0x0043))

    def test_real_unplugged_status_is_not_charging(self) -> None:
        self.assertFalse(protocol.is_bms_charging(0x0003))


class PersistentConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_reuses_an_active_gatt_link(self) -> None:
        class FakeBleakClient:
            is_connected = True

            async def start_notify(self, _uuid: str, _callback: Any) -> None:
                return None

            async def disconnect(self) -> None:
                self.is_connected = False

        bleak_client = FakeBleakClient()
        establish = AsyncMock(return_value=bleak_client)
        client = protocol.DownGScooterClient(object())

        with patch.object(protocol, "establish_connection", establish):
            await client.connect()
            await client.connect()

        self.assertEqual(establish.await_count, 1)

    async def test_dead_gatt_link_is_replaced(self) -> None:
        class FakeBleakClient:
            def __init__(self) -> None:
                self.is_connected = True

            async def start_notify(self, _uuid: str, _callback: Any) -> None:
                return None

            async def disconnect(self) -> None:
                self.is_connected = False

        first = FakeBleakClient()
        second = FakeBleakClient()
        establish = AsyncMock(side_effect=(first, second))
        client = protocol.DownGScooterClient(object())

        with patch.object(protocol, "establish_connection", establish):
            await client.connect()
            first.is_connected = False
            await client.connect()

        self.assertEqual(establish.await_count, 2)
        self.assertTrue(client.is_connected)

    async def test_cell_timeout_keeps_cached_values_and_backs_off(self) -> None:
        client = protocol.DownGScooterClient(object())
        cells = bytes.fromhex(
            "ec 0e ee 0e ef 0e f1 0e eb 0e 06 0f 07 0f 0a 0f 07 0f 09 0f"
        )
        read_optional = AsyncMock(side_effect=(cells, None))

        with patch.object(client, "_read_optional", read_optional):
            self.assertEqual(await client._read_cells(), cells)
            self.assertEqual(await client._read_cells(), cells)
            self.assertEqual(await client._read_cells(), cells)

        self.assertEqual(read_optional.await_count, 2)
        self.assertGreater(client._cells_retry_after, 0)


if __name__ == "__main__":
    unittest.main()
