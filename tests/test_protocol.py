"""Unit tests for the Xiaomi protocol bytes recovered from DownG."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

PROTOCOL_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "downg_scooter"
    / "protocol.py"
)
SPEC = importlib.util.spec_from_file_location("downg_protocol_test", PROTOCOL_PATH)
assert SPEC is not None and SPEC.loader is not None
protocol = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = protocol
SPEC.loader.exec_module(protocol)


class XiaomiFrameTest(unittest.TestCase):
    def test_lock_state_read_frame_matches_downg(self) -> None:
        frame = protocol._build_xiaomi_frame(
            destination=0x20,
            command=0x01,
            argument=0xB2,
            payload=b"\x02",
        )
        self.assertEqual(frame, bytes.fromhex("55 AA 03 20 01 B2 02 27 FF"))

    def test_lock_write_frame_matches_downg(self) -> None:
        frame = protocol._build_xiaomi_frame(
            destination=0x20,
            command=0x03,
            argument=0x70,
            payload=b"\x01\x00",
        )
        self.assertEqual(frame, bytes.fromhex("55 AA 04 20 03 70 01 00 67 FF"))

    def test_unlock_write_frame_matches_downg(self) -> None:
        frame = protocol._build_xiaomi_frame(
            destination=0x20,
            command=0x03,
            argument=0x71,
            payload=b"\x01\x00",
        )
        self.assertEqual(frame, bytes.fromhex("55 AA 04 20 03 71 01 00 66 FF"))

    def test_extract_response_payload(self) -> None:
        frame = bytes.fromhex("55 AA 04 23 01 31 64 00 42 FF")
        self.assertEqual(
            protocol._extract_response_payload(frame, 0x01, 0x31), b"\x64\x00"
        )

    def test_fragmented_notification_is_reassembled(self) -> None:
        client = protocol.DownGScooterClient("00:11:22:33:44:55")
        frame = bytes.fromhex("55 AA 04 23 01 31 64 00 42 FF")
        client._handle_notify(None, bytearray(frame[:4]))
        self.assertTrue(client._response_queue.empty())
        client._handle_notify(None, bytearray(frame[4:]))
        self.assertEqual(client._response_queue.get_nowait(), frame)

    def test_bcd_version_and_battery_date(self) -> None:
        self.assertEqual(protocol._decode_bcd_version(0x42, 0x01), "1.4.2")
        packed_date = ((2021 - 2000) << 9) | (7 << 5) | 19
        self.assertEqual(protocol._decode_battery_date(packed_date), "2021-07-19")


if __name__ == "__main__":
    unittest.main()
