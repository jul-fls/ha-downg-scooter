# DownG Scooter for Home Assistant

Experimental Home Assistant custom integration for local Bluetooth telemetry and
software lock control of owner-controlled Xiaomi M365-family scooters.

The integration is intentionally limited to interoperability:

- read battery, cell, speed, temperature, odometer, serial, and firmware data;
- expose the Xiaomi software lock as a Home Assistant switch;
- communicate locally over Bluetooth, without a cloud account;
- no firmware flashing, speed tuning, exploit logic, or authentication bypass.

## Compatibility

The protocol implementation was derived from `com.m365downgrade_38.apk`
(SHA-256 `DF743AC77A4CBEB89FD39A63B420D8596F1DF47A80CEA1B84201A3D58AFA2706`).
It currently supports the plain Xiaomi `55 AA` protocol used by original
M365-family firmware.

Newer Xiaomi 1S, Pro 2, Essential, and Mi Scooter firmware may negotiate an
authenticated or encrypted protocol. That protocol is not implemented yet, so
compatibility depends on the scooter firmware as well as the commercial model.
See [the reverse-engineering notes](docs/reverse-engineering.md) for the observed
registers, commands, and current limitations.

## Installation with HACS

This repository is not in the default HACS catalog yet. Add it as a custom
repository:

1. Open HACS in Home Assistant.
2. Select the three-dot menu, then **Custom repositories**.
3. Enter `https://github.com/jul-fls/ha-downg-scooter`.
4. Select **Integration** as the category and add the repository.
5. Install **DownG Scooter**, then restart Home Assistant.
6. Open **Settings > Devices & services > Add integration** and select
   **DownG Scooter**.

The Home Assistant host must have a working Bluetooth adapter, or access to a
supported Bluetooth proxy. When a compatible scooter is switched on, Home
Assistant automatically proposes it as a discovered device. Manual setup by
Bluetooth address remains available as a fallback.

Some scooter firmware requires physical confirmation for a new Bluetooth
client. The setup flow detects a connected but silent scooter and only then asks
you to submit the step and press the scooter power button once when it beeps. If
confirmation is required again later, Home Assistant creates one persistent
administrator notification and removes it automatically after a successful
refresh.

Discovery uses the scooter's advertised BLE name and service signature, such as
`MIScooter...` or `xiaomi.scooter...`. Xiaomi scooters commonly use randomized
BLE addresses, so a manufacturer MAC prefix alone is not reliable.

## Entities

The integration creates one device with:

- a software lock switch;
- battery percentage, voltage, current, temperature, and capacity sensors;
- minimum, maximum, and delta cell-voltage sensors;
- charge-cycle, speed, odometer, serial-number, firmware-version, and error
  sensors.

Data is polled over BLE. The scooter must be powered on and within Bluetooth
range. Locking is a software command and must not be treated as a physical
anti-theft device.

## Development

The local toolchain follows the same layout as `ha-centoaccess` and requires
Python 3.13:

```powershell
.\scripts\setup_dev.ps1
.\.venv\Scripts\pyright.exe
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\check_version.py
```

CI runs strict Pyright checks, unit tests, manifest-version validation,
Home Assistant Hassfest, and HACS validation.

## Releases and versioning

The integration version lives in
`custom_components/downg_scooter/manifest.json`. Release tags use `vX.Y.Z` and
must exactly match the manifest version.

1. Update the manifest version.
2. Run the local checks above.
3. Merge the change after CI succeeds.
4. Create a GitHub release with a matching tag, for example `v0.4.0`.

HACS installs the assets from the GitHub release. Do not create a release tag
whose version differs from the manifest.

## Branding

The integration includes local Home Assistant brand assets in
`custom_components/downg_scooter/brand/`: standard and high-resolution icons,
plus standard and high-resolution landscape logos. Local custom-integration
branding is supported by Home Assistant 2026.3 and newer.

## License

Licensed under the Apache License 2.0.
