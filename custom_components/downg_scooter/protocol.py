"""Xiaomi M365 BLE protocol client derived from DownG interoperability analysis.

Only owner-oriented telemetry and the software lock are implemented. Firmware
flashing, tuning, and authentication bypasses are intentionally out of scope.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import logging
from typing import Final

from bleak import BleakClient
from bleak.backends.device import BLEDevice

NUS_SERVICE_UUID: Final = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_WRITE_UUID: Final = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY_UUID: Final = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

ADDR_ESC: Final = 0x20
ADDR_BMS: Final = 0x22

CMD_READ: Final = 0x01
CMD_WRITE_NO_RESPONSE: Final = 0x03

REG_ESC_INFO: Final = 0x10
REG_RUNTIME: Final = 0xB0
REG_LOCK_CONTROL: Final = 0x70
REG_UNLOCK_CONTROL: Final = 0x71

REG_BMS_INFO: Final = 0x10
REG_BMS_RUNTIME: Final = 0x31
REG_BMS_CELLS: Final = 0x40

CONNECT_SETTLE_SECONDS: Final = 0.35
REGISTER_READ_ATTEMPTS: Final = 3
REGISTER_RESPONSE_TIMEOUT: Final = 4

_LOGGER = logging.getLogger(__name__)


class ScooterProtocolError(Exception):
    """Raised when the scooter cannot be queried or returns invalid data."""


class ScooterConfirmationRequired(ScooterProtocolError):
    """Raised when the scooter connects but requires physical confirmation."""


@dataclass(slots=True)
class ScooterData:
    """Normalized Xiaomi scooter telemetry."""

    battery_percent: int | None = None
    battery_remaining_mah: int | None = None
    battery_voltage_v: float | None = None
    battery_current_a: float | None = None
    battery_temperature_c: float | None = None
    battery_cell_min_v: float | None = None
    battery_cell_max_v: float | None = None
    battery_cell_delta_mv: int | None = None
    battery_factory_capacity_mah: int | None = None
    battery_charge_cycles: int | None = None
    battery_serial: str | None = None
    battery_manufacture_date: str | None = None
    bms_version: str | None = None
    drv_version: str | None = None
    scooter_serial: str | None = None
    error_code: int | None = None
    speed_kmh: float | None = None
    odometer_km: float | None = None
    locked: bool | None = None


@dataclass(slots=True)
class _StaticInfo:
    """Values that do not need to be queried at every coordinator refresh."""

    battery_factory_capacity_mah: int | None = None
    battery_charge_cycles: int | None = None
    battery_serial: str | None = None
    battery_manufacture_date: str | None = None
    bms_version: str | None = None
    drv_version: str | None = None
    scooter_serial: str | None = None


class DownGScooterClient:
    """BLE client for Xiaomi M365-family scooters using the plain 55AA protocol."""

    def __init__(self, device: BLEDevice | str) -> None:
        """Initialize client."""
        self._device = device
        self._client: BleakClient | None = None
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._rx_buffer = bytearray()
        self._recent_notifications: deque[bytes] = deque(maxlen=5)
        self._alternate_protocol_seen = False
        self._request_lock = asyncio.Lock()
        self._static_info: _StaticInfo | None = None

    async def connect(self) -> None:
        """Connect and subscribe to Xiaomi UART notifications."""
        if self._client and self._client.is_connected:
            return

        client = BleakClient(self._device, timeout=15)
        self._client = client
        await client.connect()
        await client.start_notify(NUS_NOTIFY_UUID, self._handle_notify)
        # Some M365 BLE firmwares acknowledge the CCCD write before their UART
        # bridge is ready to forward the first command.
        await asyncio.sleep(CONNECT_SETTLE_SECONDS)

    async def disconnect(self) -> None:
        """Disconnect if connected."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        self._rx_buffer.clear()
        self._recent_notifications.clear()
        self._alternate_protocol_seen = False

    def set_device(self, device: BLEDevice | str) -> None:
        """Update the HA-selected local or remote Bluetooth device."""
        self._device = device

    async def read_telemetry(self) -> ScooterData:
        """Read the registers used by DownG for M365-family scooters."""
        await self.connect()

        bms = await self._read_register(ADDR_BMS, REG_BMS_RUNTIME, 10)
        runtime = await self._read_register(ADDR_ESC, REG_RUNTIME, 24)
        cells = await self._read_register(ADDR_BMS, REG_BMS_CELLS, 20)

        if self._static_info is None:
            self._static_info = await self._read_static_info()
        static = self._static_info

        cell_voltages = [
            _u16le_at(cells, offset) / 1000
            for offset in range(0, len(cells) - 1, 2)
            if _u16le_at(cells, offset) > 0
        ]
        temp_values = [value - 20 for value in bms[8:10]]

        return ScooterData(
            battery_percent=_u16le_at(bms, 2),
            battery_remaining_mah=_u16le_at(bms, 0),
            battery_current_a=round(_s16le_at(bms, 4) / 100, 2),
            battery_voltage_v=round(_u16le_at(bms, 6) / 100, 2),
            battery_temperature_c=round(sum(temp_values) / len(temp_values), 1),
            battery_cell_min_v=min(cell_voltages) if cell_voltages else None,
            battery_cell_max_v=max(cell_voltages) if cell_voltages else None,
            battery_cell_delta_mv=(
                round((max(cell_voltages) - min(cell_voltages)) * 1000)
                if cell_voltages
                else None
            ),
            battery_factory_capacity_mah=static.battery_factory_capacity_mah,
            battery_charge_cycles=static.battery_charge_cycles,
            battery_serial=static.battery_serial,
            battery_manufacture_date=static.battery_manufacture_date,
            bms_version=static.bms_version,
            drv_version=static.drv_version,
            scooter_serial=static.scooter_serial,
            error_code=_u16le_at(runtime, 0),
            speed_kmh=round(_u16le_at(runtime, 10) / 1000, 2),
            odometer_km=round(_u32le_at(runtime, 14) / 1000, 3),
            locked=bool(_u16le_at(runtime, 4) & 0x02),
        )

    async def set_locked(self, locked: bool) -> None:
        """Set the Xiaomi software lock using DownG's DRV registers."""
        await self.connect()
        register = REG_LOCK_CONTROL if locked else REG_UNLOCK_CONTROL
        frame = _build_xiaomi_frame(
            destination=ADDR_ESC,
            command=CMD_WRITE_NO_RESPONSE,
            argument=register,
            payload=b"\x01\x00",
        )
        async with self._request_lock:
            await self._write(frame)

    async def probe(self) -> None:
        """Verify that the scooter accepts register reads."""
        await self.connect()
        await self._read_register(ADDR_BMS, REG_BMS_RUNTIME, 10)

    async def _read_static_info(self) -> _StaticInfo:
        bms = await self._read_register(ADDR_BMS, REG_BMS_INFO, 34)
        esc = await self._read_register(ADDR_ESC, REG_ESC_INFO, 28)
        date_value = _u16le_at(bms, 32)
        return _StaticInfo(
            battery_factory_capacity_mah=_u16le_at(bms, 18),
            battery_charge_cycles=_u16le_at(bms, 22),
            battery_serial=_decode_serial(bms[0:14]),
            battery_manufacture_date=_decode_battery_date(date_value),
            bms_version=_decode_bcd_version(bms[14], bms[15]),
            drv_version=_decode_bcd_version(esc[20], esc[21]),
            scooter_serial=_decode_serial(esc[0:14]),
        )

    async def _read_register(self, destination: int, register: int, length: int) -> bytes:
        frame = _build_xiaomi_frame(
            destination=destination,
            command=CMD_READ,
            argument=register,
            payload=bytes([length]),
        )
        async with self._request_lock:
            _drain_queue(self._response_queue)
            response: bytes | None = None
            for attempt in range(1, REGISTER_READ_ATTEMPTS + 1):
                _LOGGER.debug(
                    "Reading scooter register 0x%02X, attempt %d/%d: %s",
                    register,
                    attempt,
                    REGISTER_READ_ATTEMPTS,
                    frame.hex(" "),
                )
                await self._write(frame)
                try:
                    response = await self._read_response(CMD_READ, register)
                    break
                except TimeoutError:
                    if attempt == REGISTER_READ_ATTEMPTS:
                        raise self._timeout_error(register) from None
                    await asyncio.sleep(0.25)

            if response is None:
                raise self._timeout_error(register)
        payload = _extract_response_payload(response, CMD_READ, register)
        if len(payload) != length:
            raise ScooterProtocolError(
                f"Register 0x{register:02X} returned {len(payload)} bytes, expected {length}"
            )
        return payload

    async def _write(self, frame: bytes) -> None:
        if not self._client or not self._client.is_connected:
            raise ScooterProtocolError("Scooter is not connected")
        for offset in range(0, len(frame), 20):
            await self._client.write_gatt_char(
                NUS_WRITE_UUID, frame[offset : offset + 20], response=False
            )

    async def _read_response(self, command: int, argument: int) -> bytes:
        async def wait_for_match() -> bytes:
            while True:
                frame = await self._response_queue.get()
                if len(frame) >= 6 and frame[4] == command and frame[5] == argument:
                    return frame

        return await asyncio.wait_for(
            wait_for_match(), timeout=REGISTER_RESPONSE_TIMEOUT
        )

    def _timeout_diagnostic(self, register: int) -> str:
        """Describe why a register response could not be matched."""
        prefix = f"Timed out waiting for register 0x{register:02X}"
        if self._alternate_protocol_seen:
            return (
                f"{prefix}; the scooter sent 5A A5 protocol frames, which are "
                "not supported yet"
            )
        if not self._recent_notifications:
            return f"{prefix}; no BLE notification was received"
        recent = " | ".join(data.hex(" ") for data in self._recent_notifications)
        return f"{prefix}; recent BLE notifications: {recent}"

    def _timeout_error(self, register: int) -> ScooterProtocolError:
        """Classify a silent connected scooter as awaiting confirmation."""
        message = self._timeout_diagnostic(register)
        if not self._recent_notifications:
            return ScooterConfirmationRequired(message)
        return ScooterProtocolError(message)

    def _handle_notify(self, _sender: object, data: bytearray) -> None:
        notification = bytes(data)
        self._recent_notifications.append(notification)
        _LOGGER.debug("Scooter BLE notification: %s", notification.hex(" "))
        self._rx_buffer.extend(data)
        while True:
            header = self._rx_buffer.find(b"\x55\xaa")
            if header < 0:
                if b"\x5a\xa5" in self._rx_buffer:
                    self._alternate_protocol_seen = True
                # Keep a possible first header byte when 55 AA is split across
                # two BLE notifications.
                if self._rx_buffer.endswith(b"\x55"):
                    self._rx_buffer[:] = b"\x55"
                else:
                    self._rx_buffer.clear()
                return
            if header:
                del self._rx_buffer[:header]
            if len(self._rx_buffer) < 3:
                return
            frame_length = self._rx_buffer[2] + 6
            if len(self._rx_buffer) < frame_length:
                return
            frame = bytes(self._rx_buffer[:frame_length])
            del self._rx_buffer[:frame_length]
            try:
                _validate_xiaomi_frame(frame)
            except ScooterProtocolError:
                continue
            self._response_queue.put_nowait(frame)


def _build_xiaomi_frame(
    *, destination: int, command: int, argument: int, payload: bytes
) -> bytes:
    """Build the exact plain 55AA frame produced by DownG's protocol class."""
    body = bytes([len(payload) + 2, destination, command, argument]) + payload
    checksum = (0xFFFF ^ sum(body)) & 0xFFFF
    return b"\x55\xaa" + body + checksum.to_bytes(2, "little")


def _validate_xiaomi_frame(frame: bytes) -> None:
    if len(frame) < 8 or frame[:2] != b"\x55\xaa":
        raise ScooterProtocolError("Invalid Xiaomi response header")
    expected_length = frame[2] + 6
    if len(frame) != expected_length:
        raise ScooterProtocolError("Invalid Xiaomi response length")
    expected_checksum = (0xFFFF ^ sum(frame[2:-2])) & 0xFFFF
    actual_checksum = int.from_bytes(frame[-2:], "little")
    if expected_checksum != actual_checksum:
        raise ScooterProtocolError("Invalid Xiaomi response checksum")


def _extract_response_payload(frame: bytes, command: int, argument: int) -> bytes:
    _validate_xiaomi_frame(frame)
    if frame[4] != command or frame[5] != argument:
        raise ScooterProtocolError("Xiaomi response does not match request")
    return frame[6:-2]


def _drain_queue(queue: asyncio.Queue[bytes]) -> None:
    while not queue.empty():
        queue.get_nowait()


def _decode_serial(data: bytes) -> str | None:
    value = data.rstrip(b"\x00\xff ").decode("ascii", errors="ignore")
    return value or None


def _decode_bcd_version(low: int, high: int) -> str:
    return f"{high}.{(low >> 4) & 0x0F}.{low & 0x0F}"


def _decode_battery_date(value: int) -> str | None:
    if value == 0:
        return None
    year = (value >> 9) + 2000
    month = (value >> 5) & 0x0F
    day = value & 0x1F
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _u16le_at(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=False)


def _s16le_at(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=True)


def _u32le_at(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little", signed=False)
