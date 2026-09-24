"""Xiaomi M365 BLE protocol client derived from DownG interoperability analysis.

Only owner-oriented telemetry and the software lock are implemented. Firmware
flashing, tuning, and authentication bypasses are intentionally out of scope.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import hashlib
import logging
import secrets
from typing import Final, TypeVar

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

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
AUTH_RESPONSE_TIMEOUT: Final = 5
AUTH_CONFIRMATION_TIMEOUT: Final = 20

AUTH_ADDRESS: Final = 0x21
AUTH_CHALLENGE: Final = 0x5B
AUTH_CONFIRM: Final = 0x5C
AUTH_COMPLETE: Final = 0x5D
AUTH_FIXED_BLOCK: Final = bytes.fromhex(
    "97 CF B8 02 84 41 43 DE 56 00 2B 3B 34 78 0A 5D"
)

_LOGGER = logging.getLogger(__name__)
_QueueItem = TypeVar("_QueueItem")


class ScooterProtocolError(Exception):
    """Raised when the scooter cannot be queried or returns invalid data."""


class ScooterConfirmationRequired(ScooterProtocolError):
    """Raised when the scooter connects but requires physical confirmation."""


@dataclass(slots=True)
class _EncryptedMessage:
    """Decoded message carried by DownG's authenticated 5AA5 protocol."""

    source: int
    destination: int
    command: int
    status: int
    payload: bytes


class _EncryptedSession:
    """DownG-compatible authenticated 5AA5 session state."""

    def __init__(self, scooter_name: str, app_nonce: bytes | None = None) -> None:
        name = scooter_name.encode("utf-8")[:16].ljust(16, b"\x00")
        self._name = name
        self.app_nonce = app_nonce or secrets.token_bytes(16)
        if len(self.app_nonce) != 16:
            raise ValueError("The application nonce must contain 16 bytes")
        self.scooter_nonce = bytes(16)
        self.completion_payload = bytes(14)
        self.key = _derive_auth_key(self._name, AUTH_FIXED_BLOCK)
        self.counter = 0
        self.confirmed = False

    def build_frame(
        self, destination: int, command: int, status: int = 0, payload: bytes = b""
    ) -> bytes:
        """Encrypt one command exactly like DownG's 5AA5 codec."""
        message = bytes([0x3E, destination, command, status]) + payload
        if self.counter == 0:
            encrypted = _legacy_auth_crypt(message, self.key)
            tag = _legacy_auth_checksum(message)
            wire_counter = 0
            self.counter = 1
        else:
            self.counter += 1
            wire_counter = self.counter
            encrypted = _auth_ctr_crypt(
                message, self.key, self.scooter_nonce, wire_counter
            )
            tag = _auth_tag(message, self.key, self.scooter_nonce, wire_counter)
        return (
            b"\x5a\xa5"
            + bytes([len(payload)])
            + encrypted
            + tag
            + wire_counter.to_bytes(2, "big")
        )

    def decode_frame(self, frame: bytes) -> _EncryptedMessage:
        """Decrypt one scooter response and advance the session state."""
        if len(frame) < 13 or frame[:2] != b"\x5a\xa5":
            raise ScooterProtocolError("Invalid encrypted Xiaomi response header")
        if len(frame) != frame[2] + 13:
            raise ScooterProtocolError("Invalid encrypted Xiaomi response length")

        wire_counter = int.from_bytes(frame[-2:], "big")
        encrypted = frame[3:-6]
        if wire_counter == 0:
            decoded = _legacy_auth_crypt(encrypted, self.key)
        else:
            if wire_counter <= self.counter:
                raise ScooterProtocolError("Stale encrypted Xiaomi response counter")
            decoded = _auth_ctr_crypt(
                encrypted, self.key, self.scooter_nonce, wire_counter
            )
            self.counter = wire_counter
        if len(decoded) != frame[2] + 4:
            raise ScooterProtocolError("Invalid encrypted Xiaomi message length")

        message = _EncryptedMessage(
            source=decoded[0],
            destination=decoded[1],
            command=decoded[2],
            status=decoded[3],
            payload=decoded[4:],
        )
        if (
            message.source == AUTH_ADDRESS
            and message.command == AUTH_CHALLENGE
            and len(message.payload) == 30
        ):
            self.scooter_nonce = message.payload[:16]
            self.completion_payload = message.payload[16:]
            self.key = _derive_auth_key(self._name, self.scooter_nonce)
        elif (
            message.source == AUTH_ADDRESS
            and message.command == AUTH_CONFIRM
            and message.status == 1
        ):
            self.key = _derive_auth_key(self.app_nonce, self.scooter_nonce)
            self.confirmed = True
        return message


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
    """BLE client for Xiaomi M365-family scooters supported by DownG."""

    def __init__(
        self,
        device: BLEDevice | str,
        *,
        scooter_name: str = "MIScooter",
        encrypted: bool = False,
    ) -> None:
        """Initialize client."""
        self._device = device
        self._scooter_name = scooter_name
        self._encrypted_required = encrypted
        self._client: BleakClient | None = None
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._encrypted_response_queue: asyncio.Queue[_EncryptedMessage] = (
            asyncio.Queue()
        )
        self._encrypted_session: _EncryptedSession | None = None
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
        self._encrypted_session = None

    def set_device(self, device: BLEDevice | str) -> None:
        """Update the HA-selected local or remote Bluetooth device."""
        self._device = device

    async def read_telemetry(self) -> ScooterData:
        """Read the registers used by DownG for M365-family scooters."""
        await self.connect()
        await self._ensure_authenticated()

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
        await self._ensure_authenticated()
        register = REG_LOCK_CONTROL if locked else REG_UNLOCK_CONTROL
        frame = self._build_frame(
            ADDR_ESC, CMD_WRITE_NO_RESPONSE, register, b"\x01\x00"
        )
        async with self._request_lock:
            await self._write(frame)

    async def probe(self) -> None:
        """Verify that the scooter accepts register reads."""
        await self.connect()
        await self._read_register(ADDR_BMS, REG_BMS_RUNTIME, 10)

    async def async_begin_authentication(self) -> bool:
        """Start DownG authentication and report whether a button press is needed."""
        await self.connect()
        self._encrypted_session = _EncryptedSession(self._scooter_name)
        _drain_queue(self._encrypted_response_queue)

        await self._write(
            self._encrypted_session.build_frame(AUTH_ADDRESS, AUTH_CHALLENGE)
        )
        try:
            challenge = await self._read_encrypted_response(AUTH_CHALLENGE)
        except TimeoutError as err:
            raise ScooterProtocolError(
                "Timed out waiting for the 0x5B authentication challenge"
            ) from err
        if len(challenge.payload) != 30:
            raise ScooterProtocolError("Invalid 0x5B authentication challenge")

        await self._write(
            self._encrypted_session.build_frame(
                AUTH_ADDRESS,
                AUTH_CONFIRM,
                payload=self._encrypted_session.app_nonce,
            )
        )
        try:
            confirmation = await self._read_encrypted_response(AUTH_CONFIRM)
        except TimeoutError as err:
            raise ScooterProtocolError(
                "Timed out waiting for the initial 0x5C authentication response"
            ) from err
        if confirmation.status == 1:
            await self._complete_authentication()
            return False
        if confirmation.status != 0:
            raise ScooterProtocolError(
                f"Scooter rejected authentication with status {confirmation.status}"
            )
        return True

    async def async_finish_authentication(self) -> None:
        """Wait for the physical button confirmation and complete authentication."""
        if self._encrypted_session is None:
            raise ScooterProtocolError("Authentication has not been started")
        try:
            confirmation = await self._read_encrypted_response(
                AUTH_CONFIRM, timeout=AUTH_CONFIRMATION_TIMEOUT
            )
        except TimeoutError as err:
            raise ScooterConfirmationRequired(
                "Timed out waiting for the scooter button confirmation"
            ) from err
        if confirmation.status != 1:
            raise ScooterConfirmationRequired(
                f"Unexpected scooter confirmation status {confirmation.status}"
            )
        await self._complete_authentication()

    async def _complete_authentication(self) -> None:
        """Send the final 0x5D proof after a successful confirmation."""
        session = self._encrypted_session
        if session is None or not session.confirmed:
            raise ScooterProtocolError("Scooter authentication was not confirmed")
        await self._write(
            session.build_frame(
                AUTH_ADDRESS, AUTH_COMPLETE, payload=session.completion_payload
            )
        )
        try:
            await self._read_encrypted_response(AUTH_COMPLETE)
        except TimeoutError as err:
            raise ScooterProtocolError(
                "Timed out waiting for the 0x5D authentication completion"
            ) from err
        self._encrypted_required = True

    async def _ensure_authenticated(self) -> None:
        """Open an encrypted session for entries that negotiated 5AA5."""
        if not self._encrypted_required or self._encrypted_session is not None:
            return
        confirmation_required = await self.async_begin_authentication()
        if confirmation_required:
            raise ScooterConfirmationRequired(
                "The scooter requested a physical button confirmation"
            )

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
        async with self._request_lock:
            _drain_queue(self._response_queue)
            response: bytes | None = None
            for attempt in range(1, REGISTER_READ_ATTEMPTS + 1):
                frame = self._build_frame(
                    destination, CMD_READ, register, bytes([length])
                )
                _LOGGER.debug(
                    "Reading scooter register 0x%02X, attempt %d/%d: %s",
                    register,
                    attempt,
                    REGISTER_READ_ATTEMPTS,
                    frame.hex(" "),
                )
                await self._write(frame)
                try:
                    if self._encrypted_session is None:
                        response = await self._read_response(CMD_READ, register)
                    else:
                        encrypted_response = await self._read_encrypted_response(
                            CMD_READ, status=register
                        )
                        response = encrypted_response.payload
                    break
                except TimeoutError:
                    if attempt == REGISTER_READ_ATTEMPTS:
                        raise self._timeout_error(register) from None
                    await asyncio.sleep(0.25)

            if response is None:
                raise self._timeout_error(register)
        payload = (
            response
            if self._encrypted_session is not None
            else _extract_response_payload(response, CMD_READ, register)
        )
        if len(payload) != length:
            raise ScooterProtocolError(
                f"Register 0x{register:02X} returned {len(payload)} bytes, expected {length}"
            )
        return payload

    def _build_frame(
        self, destination: int, command: int, argument: int, payload: bytes
    ) -> bytes:
        """Build a frame for the currently active transport."""
        if self._encrypted_session is not None:
            return self._encrypted_session.build_frame(
                destination, command, argument, payload
            )
        return _build_xiaomi_frame(
            destination=destination,
            command=command,
            argument=argument,
            payload=payload,
        )

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

    async def _read_encrypted_response(
        self,
        command: int,
        *,
        status: int | None = None,
        timeout: float = AUTH_RESPONSE_TIMEOUT,
    ) -> _EncryptedMessage:
        """Wait for one decoded encrypted response."""
        async def wait_for_match() -> _EncryptedMessage:
            while True:
                message = await self._encrypted_response_queue.get()
                if message.command == command and (
                    status is None or message.status == status
                ):
                    return message

        return await asyncio.wait_for(wait_for_match(), timeout=timeout)

    def _timeout_diagnostic(self, register: int) -> str:
        """Describe why a register response could not be matched."""
        prefix = f"Timed out waiting for register 0x{register:02X}"
        if self._alternate_protocol_seen:
            return (
                f"{prefix}; the scooter sent an unexpected 5A A5 protocol frame"
            )
        if not self._recent_notifications:
            return f"{prefix}; no BLE notification was received"
        recent = " | ".join(data.hex(" ") for data in self._recent_notifications)
        return f"{prefix}; recent BLE notifications: {recent}"

    def _timeout_error(self, register: int) -> ScooterProtocolError:
        """Return a protocol timeout without guessing that pairing is required."""
        return ScooterProtocolError(self._timeout_diagnostic(register))

    def _handle_notify(self, _sender: object, data: bytearray) -> None:
        notification = bytes(data)
        self._recent_notifications.append(notification)
        _LOGGER.debug("Scooter BLE notification: %s", notification.hex(" "))
        self._rx_buffer.extend(data)
        while True:
            plain_header = self._rx_buffer.find(b"\x55\xaa")
            encrypted_header = self._rx_buffer.find(b"\x5a\xa5")
            headers = [value for value in (plain_header, encrypted_header) if value >= 0]
            if not headers:
                if self._rx_buffer.endswith((b"\x55", b"\x5a")):
                    self._rx_buffer[:] = self._rx_buffer[-1:]
                else:
                    self._rx_buffer.clear()
                return
            header = min(headers)
            if header:
                del self._rx_buffer[:header]
            if len(self._rx_buffer) < 3:
                return
            encrypted = bytes(self._rx_buffer[:2]) == b"\x5a\xa5"
            if encrypted:
                self._alternate_protocol_seen = True
            frame_length = self._rx_buffer[2] + (13 if encrypted else 6)
            if len(self._rx_buffer) < frame_length:
                return
            frame = bytes(self._rx_buffer[:frame_length])
            del self._rx_buffer[:frame_length]
            if encrypted:
                if self._encrypted_session is None:
                    continue
                try:
                    message = self._encrypted_session.decode_frame(frame)
                except ScooterProtocolError:
                    _LOGGER.debug("Discarding invalid encrypted Xiaomi frame")
                    continue
                self._encrypted_response_queue.put_nowait(message)
                continue
            try:
                _validate_xiaomi_frame(frame)
            except ScooterProtocolError:
                continue
            self._response_queue.put_nowait(frame)


def _aes_encrypt(block: bytes, key: bytes) -> bytes:
    """Encrypt one 16-byte block with the AES primitive used by DownG."""
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def _derive_auth_key(first: bytes, second: bytes) -> bytes:
    """Derive the 16-byte session key from two fixed-width values."""
    material = bytearray(32)
    material[: min(len(first), 16)] = first[:16]
    material[16 : 16 + min(len(second), 16)] = second[:16]
    return hashlib.sha1(material).digest()[:16]


def _xor_bytes(first: bytes | bytearray, second: bytes | bytearray) -> bytes:
    return bytes(left ^ right for left, right in zip(first, second, strict=False))


def _legacy_auth_crypt(data: bytes, key: bytes) -> bytes:
    """Apply the initial DownG XOR stream used while the counter is zero."""
    stream = _aes_encrypt(AUTH_FIXED_BLOCK, key)
    return bytes(value ^ stream[index % 16] for index, value in enumerate(data))


def _legacy_auth_checksum(data: bytes) -> bytes:
    """Build the four-byte signed checksum used on the initial command."""
    signed_sum = sum(value if value < 128 else value - 256 for value in data)
    checksum = (~signed_sum) & 0xFFFF
    return b"\x00\x00" + checksum.to_bytes(2, "little")


def _auth_counter_block(counter: int, scooter_nonce: bytes, block: int) -> bytes:
    value = bytearray(16)
    value[0] = 1
    value[1:5] = counter.to_bytes(4, "big")
    value[5:13] = scooter_nonce[:8]
    value[15] = block & 0xFF
    return bytes(value)


def _auth_ctr_crypt(
    data: bytes, key: bytes, scooter_nonce: bytes, counter: int
) -> bytes:
    """Apply DownG's AES-CTR-compatible message encryption."""
    result = bytearray(len(data))
    for offset in range(0, len(data), 16):
        block_number = offset // 16 + 1
        stream = _aes_encrypt(
            _auth_counter_block(counter, scooter_nonce, block_number), key
        )
        chunk = data[offset : offset + 16]
        result[offset : offset + len(chunk)] = _xor_bytes(chunk, stream)
    return bytes(result)


def _auth_tag(
    data: bytes, key: bytes, scooter_nonce: bytes, counter: int
) -> bytes:
    """Calculate the four-byte authentication tag used by 5AA5 frames."""
    b0 = bytearray(16)
    b0[0] = 0x59
    b0[1:5] = counter.to_bytes(4, "big")
    b0[5:13] = scooter_nonce[:8]
    b0[15] = len(data) & 0xFF
    state = _aes_encrypt(bytes(b0), key)

    associated = bytearray(16)
    associated[0:3] = bytes([0x5A, 0xA5, len(data) - 4])
    state = _aes_encrypt(_xor_bytes(associated, state), key)
    for offset in range(0, len(data), 16):
        block = data[offset : offset + 16].ljust(16, b"\x00")
        state = _aes_encrypt(_xor_bytes(block, state), key)

    mask = _aes_encrypt(_auth_counter_block(counter, scooter_nonce, 0), key)
    return _xor_bytes(mask[:4], state[:4])


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


def _drain_queue(queue: asyncio.Queue[_QueueItem]) -> None:
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
