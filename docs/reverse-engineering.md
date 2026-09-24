# DownG Xiaomi protocol findings

Source APK: `com.m365downgrade_38.apk`  
SHA-256: `DF743AC77A4CBEB89FD39A63B420D8596F1DF47A80CEA1B84201A3D58AFA2706`

This document records only interoperability details needed for telemetry and
the owner-operated software lock. Firmware flashing, tuning, and bypass logic
are outside the integration's scope.

## Bluetooth transport

- UART service: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- Write characteristic: `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- Notify characteristic: `6e400003-b5a3-f393-e0a9-e50e24dcca9e`
- Xiaomi discovery service also referenced by the APK: `0000fe95-0000-1000-8000-00805f9b34fb`

## Plain Xiaomi frame

`55 AA LEN DEST CMD ARG PAYLOAD CHECKSUM_LE`

- `LEN` is payload length plus two bytes for `CMD` and `ARG`.
- `CHECKSUM` is `0xFFFF XOR sum(LEN..PAYLOAD)`.
- Reads use command `0x01` with the requested byte count as payload.
- Writes without a reply use command `0x03` and a little-endian 16-bit value.

## Registers observed in DownG

| Destination | Register | Meaning |
| --- | ---: | --- |
| DRV `0x20` | `0x10` | Serial, DRV version, current error block |
| DRV `0x20` | `0x29` | Odometer and accumulated run-time block |
| DRV `0x20` | `0xB0` | Error and live telemetry block |
| DRV `0x20` | `0x70` | Lock, value `0x0001` |
| DRV `0x20` | `0x71` | Unlock, value `0x0001` |
| DRV `0x20` | `0xB2` | Status flags; bit `0x02` means locked |
| BMS `0x22` | `0x10` | Serial, version, capacity, cycles, manufacture date |
| BMS `0x22` | `0x31` | Remaining capacity, percent, current, voltage, temperatures |
| BMS `0x22` | `0x40` | Cell voltages in millivolts |

DownG's main lock button reads `0xB2`, sends `0x70` when unlocked, and sends
`0x71` when locked. The exact plain frames for those writes are:

- lock: `55 AA 04 20 03 70 01 00 67 FF`
- unlock: `55 AA 04 20 03 71 01 00 66 FF`

## Authenticated Xiaomi frame

DownG contains several protocol implementations, selected from Xiaomi BLE
advertising data: plain `55 AA`, XOR-protected, and authenticated/encrypted
variants. The authenticated transport uses this outer layout:

`5A A5 PAYLOAD_LEN ENCRYPTED_MESSAGE TAG_4 COUNTER_BE`

The session starts with `0x5B`, whose 30-byte response supplies a 16-byte scooter
nonce and the 14-byte completion proof. The client sends a fresh 16-byte nonce
with `0x5C`. Status `0` requests the physical power-button confirmation; the
unsolicited status `1` response confirms it. The client then sends the 14-byte
proof with `0x5D`.

Session keys are derived with SHA-1 and truncated to 16 bytes. Message payloads
use the APK's AES block construction, four-byte authentication tag, and
big-endian message counter. A fresh client nonce is generated for every
connection. No captured key, static pairing token, or authentication bypass is
used.

The XOR-protected variants present in the APK remain unsupported.
