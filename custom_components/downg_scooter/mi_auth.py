"""Xiaomi MiAuth mode-2 registration, login, and encrypted UART transport."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import hmac
import logging
import secrets
from typing import Final

from bleak import BleakClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MI_CONTROL_UUID: Final = "00000010-0000-1000-8000-00805f9b34fb"
MI_AUTH_UUID: Final = "00000019-0000-1000-8000-00805f9b34fb"
NUS_WRITE_UUID: Final = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY_UUID: Final = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

GET_INFO: Final = bytes.fromhex("A2 00 00 00")
SET_KEY: Final = bytes.fromhex("15 00 00 00")
AUTHENTICATE: Final = bytes.fromhex("13 00 00 00")
LOGIN: Final = bytes.fromhex("24 00 00 00")
SEND_DATA: Final = bytes.fromhex("00 00 00 03 04 00")
SEND_DID: Final = bytes.fromhex("00 00 00 00 02 00")
SEND_KEY: Final = bytes.fromhex("00 00 00 0B 01 00")
SEND_INFO: Final = bytes.fromhex("00 00 00 0A 02 00")
PARCEL_DEVICE_INFO: Final = 0x00
PARCEL_PUBLIC_KEY: Final = 0x03
PARCEL_LOGIN_RANDOM: Final = 0x0D
PARCEL_LOGIN_INFO: Final = 0x0C
READY: Final = bytes.fromhex("00 00 01 01")
OK: Final = bytes.fromhex("00 00 01 00")
AUTH_OK: Final = bytes.fromhex("11 00 00 00")
AUTH_ERROR: Final = bytes.fromhex("12 00 00 00")
LOGIN_OK: Final = bytes.fromhex("21 00 00 00")
LOGIN_ERROR: Final = bytes.fromhex("23 00 00 00")

SETUP_INFO: Final = b"mible-setup-info"
LOGIN_INFO: Final = b"mible-login-info"
DID_NONCE: Final = bytes(range(0x10, 0x1C))
_LOGGER = logging.getLogger(__name__)


class MiAuthError(Exception):
    """Raised when Xiaomi MiAuth rejects or times out."""


class MiAuthConfirmationRequired(MiAuthError):
    """Raised when final registration is rejected by the scooter."""


class MiAuthRestartRequired(MiAuthError):
    """Raised when registration must restart after physical confirmation."""


@dataclass(frozen=True, slots=True)
class EncryptionKey:
    """One directional AES key and IV prefix."""

    key: bytes
    iv: bytes


@dataclass(frozen=True, slots=True)
class SessionKeys:
    """Keys negotiated for both UART directions."""

    device: EncryptionKey
    application: EncryptionKey


@dataclass(frozen=True, slots=True)
class UartMessage:
    """Decoded encrypted UART message."""

    source: int
    command: int
    argument: int
    payload: bytes


class MiAuthTransport:
    """Packet transport over Xiaomi FE95 control and authentication channels."""

    def __init__(self, client: BleakClient, *, timeout: float = 10) -> None:
        self._client = client
        self._timeout = timeout
        self._auth_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._control_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._started = False

    async def start(self) -> None:
        """Subscribe to both MiAuth characteristics."""
        if self._started:
            return
        await self._client.start_notify(MI_AUTH_UUID, self._auth_notification)
        await self._client.start_notify(MI_CONTROL_UUID, self._control_notification)
        self._started = True

    async def stop(self) -> None:
        """Release MiAuth subscriptions."""
        if not self._started:
            return
        with suppress(Exception):
            await self._client.stop_notify(MI_AUTH_UUID)
        with suppress(Exception):
            await self._client.stop_notify(MI_CONTROL_UUID)
        self._started = False

    def _auth_notification(self, _sender: object, data: bytearray) -> None:
        _LOGGER.debug("MiAuth RX auth: %s", bytes(data).hex(" "))
        self._auth_queue.put_nowait(bytes(data))

    def _control_notification(self, _sender: object, data: bytearray) -> None:
        _LOGGER.debug("MiAuth RX control: %s", bytes(data).hex(" "))
        self._control_queue.put_nowait(bytes(data))

    async def write_control(self, data: bytes) -> None:
        """Write control data."""
        await self._write(MI_CONTROL_UUID, data)

    async def write_auth(self, data: bytes) -> None:
        """Write authentication data."""
        await self._write(MI_AUTH_UUID, data)

    async def _write(self, uuid: str, data: bytes) -> None:
        for offset in range(0, len(data), 20):
            chunk = data[offset : offset + 20]
            _LOGGER.debug("MiAuth TX %s: %s", uuid, chunk.hex(" "))
            await self._client.write_gatt_char(uuid, chunk, response=False)
            await asyncio.sleep(0.02)

    async def next_auth(self, *, timeout: float | None = None) -> bytes:
        """Read one authentication notification."""
        return await asyncio.wait_for(
            self._auth_queue.get(), timeout=self._timeout if timeout is None else timeout
        )

    async def next_control(self, *, timeout: float | None = None) -> bytes:
        """Read one control notification."""
        return await asyncio.wait_for(
            self._control_queue.get(),
            timeout=self._timeout if timeout is None else timeout,
        )

    async def expect_auth(self, expected: bytes) -> None:
        """Require an exact authentication response."""
        response = await self.next_auth()
        if response != expected:
            raise MiAuthError(
                f"Expected {expected.hex(' ')}, received {response.hex(' ')}"
            )

    async def read_parcel(self, expected_command: int | None = None) -> bytes:
        """Receive and acknowledge one fragmented MiAuth parcel."""
        header = await self.next_auth()
        if len(header) < 6 or header[:3] != b"\x00\x00\x00":
            raise MiAuthError(f"Invalid MiAuth parcel header: {header.hex(' ')}")
        command = header[3]
        if expected_command is not None and command != expected_command:
            raise MiAuthError(
                "Unexpected MiAuth parcel command: "
                f"0x{command:02X}, expected 0x{expected_command:02X}"
            )
        frame_count = int.from_bytes(header[4:6], "little")
        if not 1 <= frame_count <= 32:
            raise MiAuthError(f"Invalid MiAuth parcel count: {frame_count}")

        await self.write_auth(READY)
        frames: dict[int, bytes] = {}
        while len(frames) < frame_count:
            frame = await self.next_auth()
            if len(frame) < 3:
                raise MiAuthError(f"Invalid MiAuth parcel frame: {frame.hex(' ')}")
            index = int.from_bytes(frame[:2], "little")
            if not 1 <= index <= frame_count:
                raise MiAuthError(f"Invalid MiAuth frame index: {index}")
            frames[index] = frame[2:]
        await self.write_auth(OK)
        return b"".join(frames[index] for index in range(1, frame_count + 1))

    async def write_parcel(self, data: bytes) -> None:
        """Send one fragmented MiAuth parcel."""
        await self.expect_auth(READY)
        for index, offset in enumerate(range(0, len(data), 18), start=1):
            await self.write_auth(
                index.to_bytes(2, "little") + data[offset : offset + 18]
            )
        await self.expect_auth(OK)


async def register(client: BleakClient, *, timeout: float = 10) -> bytes:
    """Register this client and return the persistent 12-byte MiAuth token."""
    transport = MiAuthTransport(client, timeout=timeout)
    await transport.start()
    try:
        return await _register(transport)
    finally:
        await transport.stop()


async def _register(transport: MiAuthTransport) -> bytes:
    await transport.write_control(GET_INFO)
    remote_info = await transport.read_parcel(PARCEL_DEVICE_INFO)
    if len(remote_info) < 8:
        raise MiAuthError(f"Invalid remote device information ({len(remote_info)} bytes)")

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )[1:]

    await transport.write_control(SET_KEY)
    await transport.write_auth(SEND_DATA)
    try:
        await transport.write_parcel(public_key)
    except TimeoutError as err:
        raise MiAuthRestartRequired("No READY response after SET_KEY/SEND_DATA") from err
    except MiAuthError as err:
        raise MiAuthRestartRequired(
            f"Unexpected response after SET_KEY/SEND_DATA: {err}"
        ) from err

    remote_public_key = await transport.read_parcel(PARCEL_PUBLIC_KEY)
    if len(remote_public_key) != 64:
        raise MiAuthError(f"Invalid scooter public key ({len(remote_public_key)} bytes)")
    peer_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b"\x04" + remote_public_key
    )
    shared_secret = private_key.exchange(ec.ECDH(), peer_key)
    derived = _hkdf(shared_secret, salt=None, info=SETUP_INFO)
    token = derived[:12]
    did_key = derived[28:44]
    did = AESCCM(did_key, tag_length=4).encrypt(DID_NONCE, remote_info[4:], b"devID")

    await transport.write_auth(SEND_DID)
    await transport.write_parcel(did)
    await transport.write_control(AUTHENTICATE)
    result = await transport.next_control()
    if result == AUTH_ERROR:
        raise MiAuthConfirmationRequired("The scooter rejected registration")
    if result != AUTH_OK:
        raise MiAuthError(f"Unexpected registration result: {result.hex(' ')}")
    return token


async def login(client: BleakClient, token: bytes, *, timeout: float = 10) -> SessionKeys:
    """Authenticate with a stored token and derive encrypted UART keys."""
    if len(token) != 12:
        raise MiAuthError("MiAuth token must contain 12 bytes")
    transport = MiAuthTransport(client, timeout=timeout)
    await transport.start()
    try:
        return await _login(transport, token)
    finally:
        await transport.stop()


async def _login(transport: MiAuthTransport, token: bytes) -> SessionKeys:
    application_random = secrets.token_bytes(16)
    await transport.write_control(LOGIN)
    await transport.write_auth(SEND_KEY)
    await transport.write_parcel(application_random)
    device_random = await transport.read_parcel(PARCEL_LOGIN_RANDOM)
    device_confirmation = await transport.read_parcel(PARCEL_LOGIN_INFO)
    if len(device_random) != 16 or len(device_confirmation) != 32:
        raise MiAuthError(
            "Invalid login challenge lengths: "
            f"{len(device_random)} and {len(device_confirmation)}"
        )

    salt = application_random + device_random
    inverse_salt = device_random + application_random
    derived = _hkdf(token, salt=salt, info=LOGIN_INFO)
    device_key = derived[:16]
    application_key = derived[16:32]
    expected_confirmation = hmac.new(
        device_key, inverse_salt, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(device_confirmation, expected_confirmation):
        raise MiAuthError("Scooter login confirmation does not match the token")

    application_confirmation = hmac.new(
        application_key, salt, hashlib.sha256
    ).digest()
    await transport.write_auth(SEND_INFO)
    await transport.write_parcel(application_confirmation)
    result = await transport.next_control()
    if result == LOGIN_ERROR:
        raise MiAuthError("Scooter rejected the stored MiAuth token")
    if result != LOGIN_OK:
        raise MiAuthError(f"Unexpected login result: {result.hex(' ')}")
    return SessionKeys(
        device=EncryptionKey(device_key, derived[32:36]),
        application=EncryptionKey(application_key, derived[36:40]),
    )


class MiUartSession:
    """Encrypted Xiaomi 55AB UART session established by MiAuth."""

    def __init__(self, keys: SessionKeys) -> None:
        self._keys = keys
        self._outgoing_counter = 0

    def build_frame(
        self, destination: int, command: int, argument: int, payload: bytes
    ) -> bytes:
        """Build one encrypted UART frame."""
        length = len(payload) + 2
        counter = self._outgoing_counter
        self._outgoing_counter += 1
        counter_bytes = counter.to_bytes(4, "little")
        nonce = self._keys.application.iv + bytes(4) + counter_bytes
        plaintext = bytes([destination, command, argument]) + payload + secrets.token_bytes(4)
        encrypted = AESCCM(self._keys.application.key, tag_length=4).encrypt(
            nonce, plaintext, None
        )
        body = bytes([length]) + counter_bytes[:2] + encrypted
        checksum = (0xFFFF ^ sum(body)) & 0xFFFF
        return b"\x55\xab" + body + checksum.to_bytes(2, "little")

    def decode_frame(self, frame: bytes) -> UartMessage:
        """Validate and decrypt one UART frame."""
        if len(frame) < 16 or frame[:2] != b"\x55\xab":
            raise MiAuthError("Invalid encrypted UART header")
        if len(frame) != frame[2] + 16:
            raise MiAuthError("Invalid encrypted UART length")
        checksum = (0xFFFF ^ sum(frame[2:-2])) & 0xFFFF
        if int.from_bytes(frame[-2:], "little") != checksum:
            raise MiAuthError("Invalid encrypted UART checksum")
        counter = frame[3:5] + bytes(2)
        nonce = self._keys.device.iv + bytes(4) + counter
        plaintext = AESCCM(self._keys.device.key, tag_length=4).decrypt(
            nonce, frame[5:-2], None
        )
        payload_length = frame[2] - 2
        if len(plaintext) != payload_length + 7:
            raise MiAuthError("Invalid encrypted UART plaintext length")
        return UartMessage(
            source=plaintext[0],
            command=plaintext[1],
            argument=plaintext[2],
            payload=plaintext[3 : 3 + payload_length],
        )


class MiUartClient:
    """Persistent encrypted UART transport for multiple scooter commands."""

    def __init__(
        self, client: BleakClient, session: MiUartSession, *, timeout: float = 10
    ) -> None:
        self._client = client
        self._session = session
        self._timeout = timeout
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = bytearray()
        self._started = False

    async def start(self) -> None:
        """Subscribe to encrypted UART notifications."""
        if self._started:
            return
        with suppress(Exception):
            await self._client.stop_notify(NUS_NOTIFY_UUID)
        await self._client.start_notify(NUS_NOTIFY_UUID, self._received)
        self._started = True

    async def stop(self) -> None:
        """Release the UART subscription."""
        if not self._started:
            return
        with suppress(Exception):
            await self._client.stop_notify(NUS_NOTIFY_UUID)
        self._started = False

    def _received(self, _sender: object, data: bytearray) -> None:
        self._buffer.extend(data)
        while True:
            header = self._buffer.find(b"\x55\xab")
            if header < 0:
                self._buffer.clear()
                return
            if header:
                del self._buffer[:header]
            if len(self._buffer) < 3:
                return
            frame_length = self._buffer[2] + 16
            if len(self._buffer) < frame_length:
                return
            frame = bytes(self._buffer[:frame_length])
            _LOGGER.debug("MiUART RX: %s", frame.hex(" "))
            self._queue.put_nowait(frame)
            del self._buffer[:frame_length]

    async def write_register(self, destination: int, register: int, payload: bytes) -> None:
        """Write one scooter register."""
        await self._send(destination, 0x03, register, payload)

    async def read_register(
        self,
        destination: int,
        register: int,
        length: int,
        *,
        timeout: float | None = None,
    ) -> bytes:
        """Read one scooter register."""
        while not self._queue.empty():
            self._queue.get_nowait()
        await self._send(destination, 0x01, register, bytes([length]))
        response_timeout = self._timeout if timeout is None else timeout
        while True:
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=response_timeout)
            except TimeoutError as err:
                raise MiAuthError(
                    f"Timed out waiting for encrypted register 0x{register:02X}"
                ) from err
            response = self._session.decode_frame(frame)
            if response.command != 0x01 or response.argument != register:
                continue
            if len(response.payload) != length:
                raise MiAuthError(
                    f"Register 0x{register:02X} returned {len(response.payload)} bytes, "
                    f"expected {length}"
                )
            return response.payload

    async def _send(
        self, destination: int, command: int, register: int, payload: bytes
    ) -> None:
        if not self._started:
            raise MiAuthError("Encrypted UART transport is not started")
        frame = self._session.build_frame(destination, command, register, payload)
        _LOGGER.debug("MiUART TX: %s", frame.hex(" "))
        for offset in range(0, len(frame), 20):
            await self._client.write_gatt_char(
                NUS_WRITE_UUID, frame[offset : offset + 20], response=False
            )


def _hkdf(secret: bytes, *, salt: bytes | None, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=64, salt=salt, info=info
    ).derive(secret)
