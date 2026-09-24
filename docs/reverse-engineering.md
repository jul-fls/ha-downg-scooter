# Xiaomi scooter protocol notes

These notes cover owner-authorized interoperability only. Firmware flashing,
tuning, exploits, and authentication bypasses are out of scope.

## Sources and validation

- DownG APK `com.m365downgrade_38.apk`, SHA-256
  `DF743AC77A4CBEB89FD39A63B420D8596F1DF47A80CEA1B84201A3D58AFA2706`;
- [`dnandha/miauth`](https://github.com/dnandha/miauth) for the Xiaomi MiAuth
  state-machine research;
- community M365 BLE/BMS register documentation;
- direct Windows BLE tests against `MIScooter0128`.

## Discovery and transport selection

The Xiaomi service-data UUID is `0000fe95-0000-1000-8000-00805f9b34fb`.
Authentication mode is encoded in FE95 frame-control bits 10-11. The tested
advertisement starts with `30 58`, which selects mode `0x02`.

UART uses Nordic UART service `6e400001-b5a3-f393-e0a9-e50e24dcca9e`, write
characteristic `...0002...`, and notify characteristic `...0003...`.

## Plain 55AA

`55 AA LEN DEST CMD ARG PAYLOAD CHECKSUM_LE`

`LEN` is payload length plus two. The checksum is
`0xFFFF XOR sum(LEN..PAYLOAD)`. Reads use command `0x01`; writes use `0x03`.

## MiAuth mode 0x02

Registration uses FE95 characteristics `0x0010` and `0x0019`, P-256 ECDH,
HKDF-SHA256 with `mible-setup-info`, and AES-CCM. It produces a persistent
12-byte token. On the tested scooter the first key exchange intentionally
times out, the scooter beeps after disconnect, and a button press opens a
five-second confirmation window. Registration is then repeated after reconnect.

Login uses fresh application/device random values, HKDF-SHA256 with
`mible-login-info`, and mutual HMAC-SHA256 confirmation. It derives separate
application and device AES keys/IV prefixes.

Authenticated UART uses `55 AB`, a little-endian packet counter, AES-CCM with a
four-byte tag, four random padding bytes, and the Xiaomi checksum. All register
reads share a single login and UART session. Home Assistant keeps that session
active while the scooter remains reachable, polls every 10 seconds, and drops
to one reconnection attempt per minute after a link failure.

This is not the unrelated `5A A5` flow initially inferred from other DownG
classes. Hardware testing established FE95 MiAuth as the correct transport for
the scooter advertisement used during development.

## Registers

| Destination | Register | Length | Meaning |
| --- | ---: | ---: | --- |
| DRV `0x20` | `0x10` | 28 | Scooter serial and DRV version |
| DRV `0x20` | `0x1A` | 2 | Main firmware version |
| DRV `0x20` | `0x25` | 2 | Estimated range, 0.01 km |
| DRV `0x20` | `0x3A` | 4 | Trip time and distance |
| DRV `0x20` | `0x67` | 4 | Extended BMS version |
| DRV `0x20` | `0x70` | 2 | Lock command, value `0x0001` |
| DRV `0x20` | `0x71` | 2 | Unlock command, value `0x0001` |
| DRV `0x20` | `0x7B` | 2 | KERS: weak `0`, medium `1`, strong `2` |
| DRV `0x20` | `0x7C` | 2 | Cruise control: off `0`, on `1` |
| DRV `0x20` | `0x7D` | 2 | Tail light: off `0`, brake `1`, always `2` |
| DRV `0x20` | `0xB0` | 32 | Errors, flags, speed, odometer, runtime |
| DRV `0x20` | `0xB5` | 2 | Dedicated signed speed |
| BMS `0x22` | `0x10` | 34 | Serial, version, capacity, cycles, date |
| BMS `0x22` | `0x30` | 2 | BMS status; bit 6 means charging |
| BMS `0x22` | `0x31` | 10 | Capacity, percent, current, voltage, temperatures |
| BMS `0x22` | `0x40` | 20 | Ten cell voltages in millivolts |

BMS current is signed: negative values mean current entering the battery. With
the charger connected, hardware testing returned status `0x0043`, `-1.73 A`,
and `38.33 V`; unplugged status was `0x0003`.
