# DownG Scooter for Home Assistant

Local Home Assistant integration for owner-controlled Xiaomi M365-family
scooters. It supports Xiaomi FE95 MiAuth pairing, encrypted `55AB` telemetry,
and the controls validated with the Windows diagnostic. Version 1.1 keeps the
authenticated BLE link active and adds adaptive 10-second/60-second polling.

The integration does not flash firmware, tune speed limits, bypass pairing, or
contact a cloud service.

## Compatibility

Two transports are supported:

- plain Xiaomi `55AA` for older compatible firmware;
- Xiaomi FE95 MiAuth mode `0x02`, with a persistent pairing token and encrypted
  `55AB` UART frames.

The MiAuth path has been validated on `MIScooter0128` with DRV `2.5.2` and BMS
`1.4.1`. Other Xiaomi firmware may expose fewer registers. The reverse
engineering details are documented in
[`docs/reverse-engineering.md`](docs/reverse-engineering.md).

## Installation with HACS

1. Open HACS and select **Custom repositories**.
2. Add `https://github.com/jul-fls/ha-downg-scooter` as an **Integration**.
3. Install **DownG Scooter** and restart Home Assistant.
4. Switch on the scooter.
5. Open **Settings > Devices & services** and select the discovered scooter.

The repository includes Home Assistant brand assets in
`custom_components/downg_scooter/brand/`.

Discovery matches scooter-specific BLE names such as `MIScooter...` and the
Xiaomi FE95 plus Nordic UART signature. Home Assistant Bluetooth matchers do
not support MAC-prefix-only discovery, and Xiaomi devices may randomize their
address, so the advertised name and service data are the reliable signals.
The tested `D4:5D:A0` Xiaomi prefix is still accepted naturally when the
scooter advertises as `MIScooter`.

Manual setup only asks for a name and Bluetooth address; there is no model
field to guess.

## MiAuth pairing

For a mode `0x02` scooter, Home Assistant shows the physical confirmation step
before starting it:

1. Select **Submit** once.
2. Wait about three seconds for the scooter to beep.
3. Press the scooter power button exactly once, within five seconds.
4. Wait while Home Assistant reconnects and stores the 12-byte pairing token.

Do not press the button unless this step is visible. A new pairing can replace
the association used by DownG or another app. If the stored token is later
rejected, Home Assistant opens its standard reauthentication flow instead of
repeating a timeout loop.

## Bluetooth proxies

The integration works with a local Bluetooth adapter or a connectable ESPHome
Bluetooth proxy. Discovery through a proxy does not prove that GATT traffic is
reliable: place the scooter close to the proxy, keep the proxy current, and
avoid having the phone connected at the same time. Pairing is timing-sensitive
and may work more reliably with a local adapter.

Once connected, the integration keeps the GATT and authenticated UART sessions
open and polls all live telemetry and setting registers every 10 seconds. If
the link is lost, it releases the dead session and retries once per minute.
After a successful reconnection, polling automatically returns to 10 seconds.

Use the included Windows diagnostic to separate scooter/protocol failures from
proxy failures:

```powershell
.\scripts\setup_diagnostic.ps1
.\.diagnostic-venv\Scripts\python.exe scripts\diagnose_scooter.py `
  --address D4:5D:A0:18:13:8D --name MIScooter0128
```

Add `--register` for the physical MiAuth registration. Its token is stored in
the ignored `.diagnostic-data/` directory and is independent from the token in
the Home Assistant config entry.

## Entities

- charging binary sensor based on BMS status bit 6;
- battery percentage, capacity, voltage, signed current, signed power, two BMS
  temperatures, status, charge cycles, and ten individual cell voltages;
- speed, average speed, odometer, trip distance/time, uptime, estimated range,
  and controller temperature;
- scooter and battery serials, manufacture date, DRV/firmware/BMS versions,
  error, warning, state flags, and work mode;
- software lock and cruise-control switches;
- tail-light and regenerative-braking selects;
- a button that flashes the tail light three times and restores its prior mode.

Negative battery current and power indicate charging. The explicit charging
entity comes from the BMS flag and is therefore preferable for automations.
The software lock is not a physical anti-theft device.

The integration keeps the BLE connection open and polls every 10 seconds while
the scooter is reachable. If Home Assistant starts while the scooter or its
Bluetooth proxy is unavailable, the config entry still loads with unavailable
entities and retries locally every 60 seconds. This avoids Home Assistant's
exponential setup backoff, which can otherwise grow to several minutes.

Cell voltages, minimum/maximum cell voltage, battery voltage, and current ask
Home Assistant to display two decimal places. Their underlying states retain
the precision supplied by the scooter.

The odometer is declared as a distance sensor with state class
`total_increasing`. Home Assistant therefore generates hourly long-term
statistics suitable for weekly, monthly, and yearly mileage calculations.
Recorder may still purge raw state history according to the instance-wide
`purge_keep_days` setting; integrations cannot override that global policy for
one entity. Long-term statistics are stored separately from that rolling raw
history.

## Development and releases

The repository follows the CI, versioning, documentation, and HACS layout of
`ha-centoaccess`:

```powershell
.\scripts\setup_dev.ps1
.\.venv\Scripts\pyright.exe
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$env:RELEASE_TAG = "v1.1.2"
.\.venv\Scripts\python.exe scripts\check_version.py
```

CI runs Pyright, unit tests, version validation, Hassfest, and HACS validation.
Release tags must exactly match `manifest.json`, using `vX.Y.Z`.

## Credits

The interoperability work builds on the DownG APK behavior, the
[`dnandha/miauth`](https://github.com/dnandha/miauth) MiAuth research, and the
community Xiaomi scooter protocol documentation. The implementation here is an
asynchronous Bleak/Home Assistant client written for this integration.

## License

Licensed under the Apache License 2.0. Review upstream license compatibility
before redistributing MiAuth-derived work under a different license.
