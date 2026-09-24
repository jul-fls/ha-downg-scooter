"""Xiaomi M365-family BLE telemetry and controls."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import logging
from time import monotonic
from typing import Final

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from .const import PROTOCOL_MIAUTH, PROTOCOL_PLAIN
from .mi_auth import MiAuthError, MiAuthRestartRequired, MiUartClient, MiUartSession
from .mi_auth import login as mi_login
from .mi_auth import register as mi_register

NUS_WRITE_UUID: Final = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY_UUID: Final = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
MI_SERVICE_UUID: Final = "0000fe95-0000-1000-8000-00805f9b34fb"

ADDR_ESC: Final = 0x20
ADDR_BMS: Final = 0x22
CMD_READ: Final = 0x01
CMD_WRITE_NO_RESPONSE: Final = 0x03

REG_ESC_INFO: Final = 0x10
REG_FIRMWARE: Final = 0x1A
REG_RANGE: Final = 0x25
REG_TRIP: Final = 0x3A
REG_LOCK_CONTROL: Final = 0x70
REG_UNLOCK_CONTROL: Final = 0x71
REG_KERS: Final = 0x7B
REG_CRUISE: Final = 0x7C
REG_TAIL_LIGHT: Final = 0x7D
REG_RUNTIME: Final = 0xB0
REG_SPEED: Final = 0xB5

REG_BMS_INFO: Final = 0x10
REG_BMS_STATUS: Final = 0x30
REG_BMS_RUNTIME: Final = 0x31
REG_BMS_CELLS: Final = 0x40
REG_BMS_VERSION_EXTENDED: Final = 0x67

REGISTER_RESPONSE_TIMEOUT: Final = 4.0
DISCOVERY_RESPONSE_TIMEOUT: Final = 1.5
PAIRING_FIRST_TIMEOUT: Final = 3.0
PAIRING_CONFIRMATION_WINDOW: Final = 5.0
PAIRING_SECOND_TIMEOUT: Final = 15.0
CELL_READ_FAILURE_RETRY_INTERVAL: Final = 60.0

TAIL_LIGHT_MODES: Final = {"off": 0, "brake": 1, "always": 2}
KERS_MODES: Final = {"weak": 0, "medium": 1, "strong": 2}

_LOGGER = logging.getLogger(__name__)


class ScooterProtocolError(Exception):
    """Raised when the scooter cannot be queried or returns invalid data."""


class ScooterAuthenticationError(ScooterProtocolError):
    """Raised when the stored MiAuth token is missing or rejected."""


@dataclass(slots=True)
class ScooterData:
    """Normalized scooter state."""

    battery_percent: int | None = None
    battery_remaining_mah: int | None = None
    battery_voltage_v: float | None = None
    battery_current_a: float | None = None
    battery_power_w: float | None = None
    battery_temperature_c: float | None = None
    battery_temperature_1_c: float | None = None
    battery_temperature_2_c: float | None = None
    battery_charging: bool | None = None
    battery_status: int | None = None
    battery_cell_voltages_v: tuple[float, ...] = ()
    battery_cell_min_v: float | None = None
    battery_cell_max_v: float | None = None
    battery_cell_delta_mv: int | None = None
    battery_factory_capacity_mah: int | None = None
    battery_charge_cycles: int | None = None
    battery_serial: str | None = None
    battery_manufacture_date: str | None = None
    bms_version: str | None = None
    bms_version_extended: str | None = None
    drv_version: str | None = None
    firmware_version: str | None = None
    scooter_serial: str | None = None
    error_code: int | None = None
    warning_code: int | None = None
    state_flags: int | None = None
    work_mode: int | None = None
    speed_kmh: float | None = None
    average_speed_kmh: float | None = None
    odometer_km: float | None = None
    trip_distance_m: int | None = None
    trip_time_s: int | None = None
    uptime_s: int | None = None
    controller_temperature_c: float | None = None
    estimated_range_km: float | None = None
    locked: bool | None = None
    cruise_enabled: bool | None = None
    tail_light_mode: str | None = None
    kers_mode: str | None = None


class DownGScooterClient:
    """BLE client for plain and MiAuth Xiaomi scooters."""

    def __init__(
        self,
        device: BLEDevice | str,
        *,
        scooter_name: str = "MIScooter",
        protocol: str = PROTOCOL_PLAIN,
        token: bytes | None = None,
    ) -> None:
        self._device = device
        self._scooter_name = scooter_name
        self._protocol = protocol
        self._token = token
        self._client: BleakClient | None = None
        self._uart: MiUartClient | None = None
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._rx_buffer = bytearray()
        self._recent_notifications: deque[bytes] = deque(maxlen=5)
        self._request_lock = asyncio.Lock()
        self._static_registers: dict[str, bytes | None] | None = None
        self._cached_cells_raw: bytes | None = None
        self._cells_retry_after = 0.0

    async def connect(self) -> None:
        """Connect using bleak-retry-connector and prepare the selected transport."""
        if self._client and self._client.is_connected:
            return
        if self._client is not None:
            self._client = None
            self._uart = None
            self._rx_buffer.clear()
            self._recent_notifications.clear()
            _drain_queue(self._response_queue)
        if isinstance(self._device, str):
            raise ScooterProtocolError("A discovered BLE device is required before connecting")

        started = monotonic()
        self._client = await establish_connection(
            BleakClient, self._device, self._scooter_name, max_attempts=3
        )
        try:
            if self._protocol == PROTOCOL_PLAIN:
                await self._client.start_notify(NUS_NOTIFY_UUID, self._handle_notify)
            elif self._protocol != PROTOCOL_MIAUTH:
                raise ScooterProtocolError(f"Unsupported scooter protocol: {self._protocol}")
        except Exception:
            await self.disconnect()
            raise
        _LOGGER.debug("Scooter BLE connection ready in %.2fs", monotonic() - started)

    async def disconnect(self) -> None:
        """Release subscriptions and disconnect."""
        if self._uart is not None:
            await self._uart.stop()
            self._uart = None
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        self._rx_buffer.clear()
        self._recent_notifications.clear()

    def set_device(self, device: BLEDevice | str) -> None:
        """Update the HA-selected local adapter or Bluetooth proxy device."""
        self._device = device

    @property
    def is_connected(self) -> bool:
        """Return whether the underlying GATT connection is still active."""
        return self._client is not None and self._client.is_connected

    async def register_miauth(self, *, timeout: float) -> bytes:
        """Perform one MiAuth registration attempt."""
        await self.connect()
        if self._client is None:
            raise ScooterProtocolError("Scooter is not connected")
        try:
            return await mi_register(self._client, timeout=timeout)
        except MiAuthRestartRequired:
            raise
        except (MiAuthError, TimeoutError) as err:
            raise ScooterProtocolError(str(err)) from err

    async def probe(self, *, timeout: float = DISCOVERY_RESPONSE_TIMEOUT) -> None:
        """Verify that the configured transport can read the BMS."""
        await self.connect()
        await self._ensure_transport()
        await self._read_register(ADDR_BMS, REG_BMS_RUNTIME, 10, timeout=timeout)

    async def read_telemetry(self) -> ScooterData:
        """Read identity, BMS, runtime, trip, range, and settings in one session."""
        started = monotonic()
        await self.connect()
        await self._ensure_transport()

        if self._static_registers is None:
            self._static_registers = {
                "esc": await self._read_register(ADDR_ESC, REG_ESC_INFO, 28),
                "firmware": await self._read_optional(ADDR_ESC, REG_FIRMWARE, 2),
                "bms_info": await self._read_register(ADDR_BMS, REG_BMS_INFO, 34),
                "bms_version": await self._read_optional(
                    ADDR_ESC, REG_BMS_VERSION_EXTENDED, 4
                ),
            }
        esc = self._static_registers["esc"]
        firmware = self._static_registers["firmware"]
        bms_info = self._static_registers["bms_info"]
        bms_version_ext = self._static_registers["bms_version"]
        if esc is None or bms_info is None:
            raise ScooterProtocolError("Static scooter information is unavailable")
        runtime = await self._read_register(ADDR_ESC, REG_RUNTIME, 32)
        trip = await self._read_optional(ADDR_ESC, REG_TRIP, 4)
        range_data = await self._read_optional(ADDR_ESC, REG_RANGE, 2)
        speed_data = await self._read_optional(ADDR_ESC, REG_SPEED, 2)
        cruise = await self._read_optional(ADDR_ESC, REG_CRUISE, 2)
        tail = await self._read_optional(ADDR_ESC, REG_TAIL_LIGHT, 2)
        kers = await self._read_optional(ADDR_ESC, REG_KERS, 2)
        bms_status_raw = await self._read_optional(ADDR_BMS, REG_BMS_STATUS, 2)
        bms = await self._read_register(ADDR_BMS, REG_BMS_RUNTIME, 10)
        cells_raw = await self._read_cells()

        current = _s16le_at(bms, 4) / 100
        voltage = _u16le_at(bms, 6) / 100
        temperatures = (float(bms[8] - 20), float(bms[9] - 20))
        cells = (
            tuple(
                round(_u16le_at(cells_raw, offset) / 1000, 3)
                for offset in range(0, len(cells_raw), 2)
                if _u16le_at(cells_raw, offset) > 0
            )
            if cells_raw
            else ()
        )
        status = _u16le_at(bms_status_raw) if bms_status_raw else None
        tail_value = _u16le_at(tail) if tail else None
        kers_value = _u16le_at(kers) if kers else None
        speed = _s16le_at(speed_data) / 1000 if speed_data else _s16le_at(runtime, 10) / 1000

        data = ScooterData(
            battery_percent=_u16le_at(bms, 2),
            battery_remaining_mah=_u16le_at(bms),
            battery_voltage_v=round(voltage, 2),
            battery_current_a=round(current, 2),
            battery_power_w=round(voltage * current, 1),
            battery_temperature_c=round(sum(temperatures) / 2, 1),
            battery_temperature_1_c=temperatures[0],
            battery_temperature_2_c=temperatures[1],
            battery_charging=is_bms_charging(status) if status is not None else None,
            battery_status=status,
            battery_cell_voltages_v=cells,
            battery_cell_min_v=min(cells) if cells else None,
            battery_cell_max_v=max(cells) if cells else None,
            battery_cell_delta_mv=round((max(cells) - min(cells)) * 1000) if cells else None,
            battery_factory_capacity_mah=_u16le_at(bms_info, 18),
            battery_charge_cycles=_u16le_at(bms_info, 22),
            battery_serial=_decode_serial(bms_info[:14]),
            battery_manufacture_date=_decode_battery_date(_u16le_at(bms_info, 32)),
            bms_version=_decode_bcd_version(bms_info[14], bms_info[15]),
            bms_version_extended=(
                _decode_bcd_version(bms_version_ext[0], bms_version_ext[1])
                if bms_version_ext else None
            ),
            drv_version=_decode_bcd_version(esc[20], esc[21]),
            firmware_version=(
                _decode_bcd_version(firmware[0], firmware[1]) if firmware else None
            ),
            scooter_serial=_decode_serial(esc[:14]),
            error_code=_u16le_at(runtime),
            warning_code=_u16le_at(runtime, 2),
            state_flags=_u16le_at(runtime, 4),
            work_mode=_u16le_at(runtime, 6),
            speed_kmh=round(speed, 2),
            average_speed_kmh=round(_u16le_at(runtime, 12) / 1000, 2),
            odometer_km=round(_u32le_at(runtime, 14) / 1000, 3),
            trip_distance_m=_s16le_at(runtime, 18),
            trip_time_s=_u16le_at(trip) if trip else None,
            uptime_s=_u16le_at(runtime, 20),
            controller_temperature_c=round(_s16le_at(runtime, 22) / 10, 1),
            estimated_range_km=round(_u16le_at(range_data) / 100, 2) if range_data else None,
            locked=bool(_u16le_at(runtime, 4) & 0x02),
            cruise_enabled=bool(_u16le_at(cruise)) if cruise else None,
            tail_light_mode=_value_name(TAIL_LIGHT_MODES, tail_value),
            kers_mode=_value_name(KERS_MODES, kers_value),
        )
        _LOGGER.debug("Scooter telemetry read in %.2fs", monotonic() - started)
        return data

    async def set_locked(self, locked: bool) -> None:
        """Set the software lock."""
        register = REG_LOCK_CONTROL if locked else REG_UNLOCK_CONTROL
        await self._write_register(ADDR_ESC, register, b"\x01\x00")

    async def set_cruise(self, enabled: bool) -> None:
        """Enable or disable cruise control."""
        await self._write_register(ADDR_ESC, REG_CRUISE, int(enabled).to_bytes(2, "little"))

    async def set_tail_light(self, mode: str) -> None:
        """Set rear-light behavior."""
        if mode not in TAIL_LIGHT_MODES:
            raise ScooterProtocolError(f"Unknown tail-light mode: {mode}")
        await self._write_register(
            ADDR_ESC, REG_TAIL_LIGHT, TAIL_LIGHT_MODES[mode].to_bytes(2, "little")
        )

    async def set_kers(self, mode: str) -> None:
        """Set regenerative braking strength."""
        if mode not in KERS_MODES:
            raise ScooterProtocolError(f"Unknown KERS mode: {mode}")
        await self._write_register(ADDR_ESC, REG_KERS, KERS_MODES[mode].to_bytes(2, "little"))

    async def flash_tail_light(self, restore_mode: str, *, count: int = 3) -> None:
        """Flash the rear light and restore its previous mode."""
        if restore_mode not in TAIL_LIGHT_MODES:
            raise ScooterProtocolError("The current tail-light mode is unknown")
        await self.connect()
        await self._ensure_transport()
        for _ in range(count):
            await self._write_register_connected(ADDR_ESC, REG_TAIL_LIGHT, b"\x02\x00")
            await asyncio.sleep(0.35)
            await self._write_register_connected(ADDR_ESC, REG_TAIL_LIGHT, b"\x00\x00")
            await asyncio.sleep(0.35)
        await self._write_register_connected(
            ADDR_ESC, REG_TAIL_LIGHT, TAIL_LIGHT_MODES[restore_mode].to_bytes(2, "little")
        )

    async def _ensure_transport(self) -> None:
        if self._protocol == PROTOCOL_PLAIN or self._uart is not None:
            return
        if self._token is None:
            raise ScooterAuthenticationError("This MiAuth scooter has no pairing token")
        if self._client is None:
            raise ScooterProtocolError("Scooter is not connected")
        try:
            keys = await mi_login(self._client, self._token)
            self._uart = MiUartClient(self._client, MiUartSession(keys))
            await self._uart.start()
        except (MiAuthError, TimeoutError) as err:
            raise ScooterAuthenticationError(str(err)) from err

    async def _read_cells(self) -> bytes | None:
        """Read cell voltages without dropping a healthy session on one timeout."""
        now = monotonic()
        if now < self._cells_retry_after:
            return self._cached_cells_raw

        cells = await self._read_optional(ADDR_BMS, REG_BMS_CELLS, 20)
        if cells is None:
            self._cells_retry_after = now + CELL_READ_FAILURE_RETRY_INTERVAL
            _LOGGER.warning(
                "BMS cell register 0x%02X did not respond; retaining the last "
                "cell values and retrying in %.0f seconds",
                REG_BMS_CELLS,
                CELL_READ_FAILURE_RETRY_INTERVAL,
            )
            return self._cached_cells_raw

        self._cached_cells_raw = cells
        self._cells_retry_after = 0.0
        return cells

    async def _read_optional(self, destination: int, register: int, length: int) -> bytes | None:
        try:
            return await self._read_register(destination, register, length)
        except ScooterProtocolError as err:
            if not self.is_connected:
                raise
            _LOGGER.debug("Optional register 0x%02X unavailable: %s", register, err)
            return None

    async def _read_register(
        self, destination: int, register: int, length: int, *, timeout: float = REGISTER_RESPONSE_TIMEOUT
    ) -> bytes:
        async with self._request_lock:
            if self._uart is not None:
                try:
                    return await self._uart.read_register(destination, register, length, timeout=timeout)
                except MiAuthError as err:
                    raise ScooterProtocolError(str(err)) from err

            _drain_queue(self._response_queue)
            await self._write_plain(_build_xiaomi_frame(
                destination=destination, command=CMD_READ, argument=register, payload=bytes([length])
            ))
            try:
                response = await self._read_response(CMD_READ, register, timeout=timeout)
            except TimeoutError as err:
                raise ScooterProtocolError(self._timeout_diagnostic(register)) from err
            payload = _extract_response_payload(response, CMD_READ, register)
            if len(payload) != length:
                raise ScooterProtocolError(
                    f"Register 0x{register:02X} returned {len(payload)} bytes, expected {length}"
                )
            return payload

    async def _write_register(self, destination: int, register: int, payload: bytes) -> None:
        await self.connect()
        await self._ensure_transport()
        await self._write_register_connected(destination, register, payload)

    async def _write_register_connected(self, destination: int, register: int, payload: bytes) -> None:
        async with self._request_lock:
            if self._uart is not None:
                try:
                    await self._uart.write_register(destination, register, payload)
                except MiAuthError as err:
                    raise ScooterProtocolError(str(err)) from err
                return
            await self._write_plain(_build_xiaomi_frame(
                destination=destination,
                command=CMD_WRITE_NO_RESPONSE,
                argument=register,
                payload=payload,
            ))

    async def _write_plain(self, frame: bytes) -> None:
        if not self._client or not self._client.is_connected:
            raise ScooterProtocolError("Scooter is not connected")
        for offset in range(0, len(frame), 20):
            await self._client.write_gatt_char(NUS_WRITE_UUID, frame[offset : offset + 20], response=False)

    async def _read_response(self, command: int, argument: int, *, timeout: float) -> bytes:
        async def wait_for_match() -> bytes:
            while True:
                frame = await self._response_queue.get()
                if len(frame) >= 6 and frame[4] == command and frame[5] == argument:
                    return frame

        return await asyncio.wait_for(wait_for_match(), timeout=timeout)

    def _handle_notify(self, _sender: object, data: bytearray) -> None:
        notification = bytes(data)
        self._recent_notifications.append(notification)
        self._rx_buffer.extend(data)
        while True:
            header = self._rx_buffer.find(b"\x55\xaa")
            if header < 0:
                self._rx_buffer[:] = self._rx_buffer[-1:] if self._rx_buffer.endswith(b"\x55") else b""
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

    def _timeout_diagnostic(self, register: int) -> str:
        prefix = f"Timed out waiting for register 0x{register:02X}"
        if not self._recent_notifications:
            return f"{prefix}; no BLE notification was received"
        recent = " | ".join(data.hex(" ") for data in self._recent_notifications)
        return f"{prefix}; recent BLE notifications: {recent}"


def protocol_mode_from_advertisement(
    manufacturer_data: dict[int, bytes], service_data: dict[str, bytes]
) -> str:
    """Select MiAuth mode 2 from Xiaomi FE95 advertisement metadata."""
    mode: int | None = None
    for company, data in manufacturer_data.items():
        payload = company.to_bytes(2, "little") + data
        if len(payload) == 8:
            mode = payload[3]
            break
    if mode is None:
        fe95 = service_data.get(MI_SERVICE_UUID)
        if fe95 is not None and len(fe95) >= 2:
            mode = (int.from_bytes(fe95[:2], "little") >> 10) & 0x03
    return PROTOCOL_MIAUTH if mode == 2 else PROTOCOL_PLAIN


def is_bms_charging(status: int) -> bool:
    """Return the charging bit confirmed against register 0x30."""
    return bool(status & (1 << 6))


def _value_name(values: dict[str, int], value: int | None) -> str | None:
    if value is None:
        return None
    return next((name for name, candidate in values.items() if candidate == value), None)


def _build_xiaomi_frame(
    *, destination: int, command: int, argument: int, payload: bytes
) -> bytes:
    body = bytes([len(payload) + 2, destination, command, argument]) + payload
    checksum = (0xFFFF ^ sum(body)) & 0xFFFF
    return b"\x55\xaa" + body + checksum.to_bytes(2, "little")


def _validate_xiaomi_frame(frame: bytes) -> None:
    if len(frame) < 8 or frame[:2] != b"\x55\xaa" or len(frame) != frame[2] + 6:
        raise ScooterProtocolError("Invalid Xiaomi response frame")
    expected = (0xFFFF ^ sum(frame[2:-2])) & 0xFFFF
    if int.from_bytes(frame[-2:], "little") != expected:
        raise ScooterProtocolError("Invalid Xiaomi response checksum")


def _extract_response_payload(frame: bytes, command: int, argument: int) -> bytes:
    _validate_xiaomi_frame(frame)
    if frame[4] != command or frame[5] != argument:
        raise ScooterProtocolError("Unexpected Xiaomi response")
    return frame[6:-2]


def _u16le_at(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _s16le_at(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=True)


def _u32le_at(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _decode_serial(data: bytes) -> str | None:
    value = data.rstrip(b"\x00\xff ").decode("ascii", errors="replace")
    return value or None


def _decode_bcd_version(first: int, second: int) -> str:
    return f"{second & 0x0F}.{first >> 4}.{first & 0x0F}"


def _decode_battery_date(value: int) -> str | None:
    year = 2000 + ((value >> 9) & 0x7F)
    month = (value >> 5) & 0x0F
    day = value & 0x1F
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _drain_queue(queue: asyncio.Queue[bytes]) -> None:
    while not queue.empty():
        queue.get_nowait()
