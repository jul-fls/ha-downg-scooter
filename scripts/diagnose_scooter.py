"""Diagnose a Xiaomi scooter directly with the computer's Bluetooth adapter."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
from pathlib import Path
import sys
from time import monotonic
from types import ModuleType

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_PATH = ROOT / "custom_components" / "downg_scooter"
MI_SERVICE_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"
MI_CONTROL_UUID = "00000010-0000-1000-8000-00805f9b34fb"
MI_AUTH_UUID = "00000019-0000-1000-8000-00805f9b34fb"
MI_GET_INFO = bytes.fromhex("A2 00 00 00")
TOKEN_DIRECTORY = ROOT / ".diagnostic-data"
PACKAGE = "downg_scooter_diagnostic"
package = ModuleType(PACKAGE)
package.__path__ = [str(INTEGRATION_PATH)]
sys.modules[PACKAGE] = package


def _load_integration_module(name: str):
    path = INTEGRATION_PATH / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


const = _load_integration_module("const")
mi_auth = _load_integration_module("mi_auth")
protocol = _load_integration_module("protocol")


def _hex(data: bytes) -> str:
    return data.hex(" ").upper()


def _matches(device: BLEDevice, address: str | None, name: str | None) -> bool:
    if address and device.address.upper() == address.upper():
        return True
    return bool(name and device.name and device.name.lower().startswith(name.lower()))


async def _scan(
    address: str | None, name: str | None, timeout: float
) -> tuple[BLEDevice, AdvertisementData] | None:
    matches: dict[str, tuple[BLEDevice, AdvertisementData]] = {}
    sightings: dict[str, tuple[BLEDevice, AdvertisementData]] = {}
    match_found = asyncio.Event()

    def detected(device: BLEDevice, advertisement: AdvertisementData) -> None:
        sightings[device.address] = (device, advertisement)
        if _matches(device, address, name):
            matches[device.address] = (device, advertisement)
            match_found.set()

    started = monotonic()
    print(f"[scan] Recherche BLE locale pendant {timeout:.1f}s...")
    scanner = BleakScanner(detection_callback=detected)
    await scanner.start()
    try:
        await asyncio.wait_for(match_found.wait(), timeout=timeout)
    except TimeoutError:
        pass
    finally:
        await scanner.stop()
    print(f"[scan] Termine en {monotonic() - started:.2f}s")

    if not matches:
        print(f"[scan] {len(sightings)} autre(s) appareil(s) BLE detecte(s).")
        named = sorted(
            (
                (item[0].name, item[0].address, item[1].rssi)
                for item in sightings.values()
                if item[0].name
            ),
            key=lambda item: item[2],
            reverse=True,
        )
        for device_name, device_address, rssi in named[:10]:
            print(f"[scan]   {device_name} ({device_address}), {rssi} dBm")
        return None
    if address:
        exact = matches.get(address.upper())
        if exact:
            return exact
    return max(matches.values(), key=lambda item: item[1].rssi)


def _print_advertisement(device: BLEDevice, advertisement: AdvertisementData) -> None:
    print(f"[scan] Appareil: {device.name or '(sans nom)'} ({device.address})")
    print(f"[scan] RSSI: {advertisement.rssi} dBm")
    if advertisement.manufacturer_data:
        for company, data in advertisement.manufacturer_data.items():
            print(f"[scan] Fabricant {company}: {_hex(data)}")
    if advertisement.service_data:
        for service, data in advertisement.service_data.items():
            print(f"[scan] Service {service}: {_hex(data)}")


def _downg_protocol_mode(advertisement: AdvertisementData) -> int | None:
    """Recover the Xiaomi authentication mode from either advertisement."""
    for company, data in advertisement.manufacturer_data.items():
        manufacturer_code = company.to_bytes(2, "little") + data
        if len(manufacturer_code) == 8:
            return manufacturer_code[3]

    service_data = advertisement.service_data.get(MI_SERVICE_UUID)
    if service_data is not None and len(service_data) >= 2:
        frame_control = int.from_bytes(service_data[:2], "little")
        return (frame_control >> 10) & 0x03
    return None


async def _probe_mi_auth_transport(
    client: protocol.DownGScooterClient, timeout: float
) -> bool:
    """Send DownG's harmless MiAuth device-information request."""
    bleak_client = client._client
    if bleak_client is None:
        raise protocol.ScooterProtocolError("BLE client is not connected")

    queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()

    def received(sender: object, data: bytearray) -> None:
        sender_uuid = str(getattr(sender, "uuid", "unknown"))
        queue.put_nowait((sender_uuid, bytes(data)))

    try:
        for uuid in (MI_CONTROL_UUID, MI_AUTH_UUID):
            characteristic = bleak_client.services.get_characteristic(uuid)
            if characteristic is None:
                raise protocol.ScooterProtocolError(
                    f"Missing Xiaomi MiAuth characteristic {uuid}"
                )
            print(
                f"[miauth] Caracteristique {uuid}: "
                f"{', '.join(characteristic.properties)}"
            )
            await bleak_client.start_notify(characteristic, received)

        print(f"[miauth] Envoi GET_INFO sur {MI_CONTROL_UUID}: {_hex(MI_GET_INFO)}")
        await bleak_client.write_gatt_char(
            MI_CONTROL_UUID, MI_GET_INFO, response=False
        )
        try:
            sender_uuid, response = await asyncio.wait_for(
                queue.get(), timeout=timeout
            )
        except TimeoutError:
            print("[miauth] Aucune notification recue apres GET_INFO.")
            return False

        print(f"[miauth] Notification {sender_uuid}: {_hex(response)}")
        return True
    finally:
        for uuid in (MI_AUTH_UUID, MI_CONTROL_UUID):
            try:
                await bleak_client.stop_notify(uuid)
            except Exception:
                pass


def _token_path(address: str) -> Path:
    safe_address = address.replace(":", "").replace("-", "").upper()
    return TOKEN_DIRECTORY / f"{safe_address}.json"


def _load_token(address: str) -> bytes | None:
    path = _token_path(address)
    if not path.exists():
        return None
    try:
        token = bytes.fromhex(json.loads(path.read_text(encoding="utf-8"))["token"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
        raise mi_auth.MiAuthError(f"Jeton local invalide: {path}") from err
    if len(token) != 12:
        raise mi_auth.MiAuthError(f"Jeton local invalide: {path}")
    return token


def _save_token(address: str, name: str, token: bytes) -> Path:
    TOKEN_DIRECTORY.mkdir(exist_ok=True)
    path = _token_path(address)
    payload = {"address": address, "name": name, "token": token.hex()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _connected_bleak_client(
    client: protocol.DownGScooterClient,
) -> BleakClient:
    bleak_client = client._client
    if bleak_client is None or not bleak_client.is_connected:
        raise protocol.ScooterProtocolError("BLE client is not connected")
    return bleak_client


async def _register_mi_auth(
    client: protocol.DownGScooterClient,
    *,
    timeout: float,
) -> bytes:
    registration_timeout = min(timeout, 3.0)
    try:
        return await mi_auth.register(
            _connected_bleak_client(client), timeout=registration_timeout
        )
    except mi_auth.MiAuthRestartRequired as first_error:
        print(f"[miauth] Premiere tentative interrompue: {first_error}")
        print("[connexion] Deconnexion pour ouvrir la confirmation physique...")
        await client.disconnect()
        print(
            "[action] Attendez le bip, puis appuyez immediatement UNE FOIS sur "
            "le bouton. Le script se reconnectera automatiquement dans 5 s."
        )
        await asyncio.sleep(5)
        print("[connexion] Reconnexion apres la fenetre de confirmation...")
        await client.connect()
        try:
            return await mi_auth.register(
                _connected_bleak_client(client), timeout=registration_timeout
            )
        except mi_auth.MiAuthRestartRequired as err:
            raise mi_auth.MiAuthError(
                "Deuxieme echange de cle sans READY apres la fenetre de "
                "confirmation: "
                f"{err}"
            ) from err


def _u16(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _s16(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=True)


def _u32(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _serial(data: bytes) -> str:
    return data.rstrip(b"\x00\xff ").decode("ascii", errors="replace") or "inconnu"


def _version(data: bytes, offset: int = 0) -> str:
    low, high = data[offset : offset + 2]
    return f"{high}.{(low >> 4) & 0x0F}.{low & 0x0F}"


def _battery_date(value: int) -> str:
    if value == 0:
        return "inconnue"
    year = (value >> 9) + 2000
    month = (value >> 5) & 0x0F
    day = value & 0x1F
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return f"inconnue (0x{value:04X})"
    return f"{year:04d}-{month:02d}-{day:02d}"


async def _read_optional(
    uart: mi_auth.MiUartClient,
    label: str,
    destination: int,
    register: int,
    length: int,
) -> bytes | None:
    last_error: mi_auth.MiAuthError | None = None
    for attempt in range(2):
        try:
            return await uart.read_register(
                destination, register, length, timeout=2.0
            )
        except mi_auth.MiAuthError as err:
            last_error = err
            if attempt == 0:
                await asyncio.sleep(0.1)
    print(f"[indisponible] {label}: {last_error}")
    return None


async def _read_full_snapshot(
    uart: mi_auth.MiUartClient,
) -> dict[str, bytes | None]:
    print("[telemetrie] Lecture complete des registres utiles...")
    requests = (
        ("esc_info", "Informations DRV", 0x20, 0x10, 28),
        ("firmware", "Version firmware", 0x20, 0x1A, 2),
        ("runtime", "Etat moteur", 0x20, 0xB0, 32),
        ("trip", "Trajet courant", 0x20, 0x3A, 4),
        ("range", "Autonomie estimee", 0x20, 0x25, 2),
        ("speed", "Vitesse", 0x20, 0xB5, 2),
        ("cruise", "Regulateur", 0x20, 0x7C, 2),
        ("tail", "Feu arriere", 0x20, 0x7D, 2),
        ("kers", "Freinage regeneratif", 0x20, 0x7B, 2),
        ("bms_info", "Informations BMS", 0x22, 0x10, 34),
        ("bms_version", "Version BMS etendue", 0x20, 0x67, 4),
        ("bms_status", "Etat de charge BMS", 0x22, 0x30, 2),
        ("bms_runtime", "Etat batterie", 0x22, 0x31, 10),
        ("cells", "Tensions cellules", 0x22, 0x40, 20),
    )
    values: dict[str, bytes | None] = {}
    for key, label, destination, register, length in requests:
        values[key] = await _read_optional(
            uart, label, destination, register, length
        )
    _print_snapshot(values)
    return values


def _print_snapshot(values: dict[str, bytes | None]) -> None:
    esc = values["esc_info"]
    firmware = values["firmware"]
    bms_info = values["bms_info"]
    bms_version = values["bms_version"]
    print("\n=== IDENTITE ET FIRMWARES ===")
    if esc:
        print(f"Numero de serie trottinette : {_serial(esc[:14])}")
        print(f"Version DRV                 : {_version(esc, 20)}")
    if firmware:
        print(f"Version firmware principale : {_version(firmware)}")
    if bms_info:
        print(f"Numero de serie batterie     : {_serial(bms_info[:14])}")
        print(f"Version BMS                  : {_version(bms_info, 14)}")
        print(f"Date fabrication batterie    : {_battery_date(_u16(bms_info, 32))}")
    if bms_version:
        print(
            "Version BMS etendue          : "
            f"{_version(bms_version)} (brut {bms_version.hex(' ')})"
        )

    bms = values["bms_runtime"]
    bms_status = values["bms_status"]
    cells_raw = values["cells"]
    print("\n=== BATTERIE ===")
    if bms_status:
        status = _u16(bms_status)
        print(
            "Charge en cours              : "
            f"{'oui' if status & (1 << 6) else 'non'} "
            f"(statut BMS 0x{status:04X})"
        )
    if bms:
        current = _s16(bms, 4) / 100
        voltage = _u16(bms, 6) / 100
        temp_1 = bms[8] - 20
        temp_2 = bms[9] - 20
        print(f"Charge                       : {_u16(bms, 2)} %")
        print(f"Capacite restante            : {_u16(bms)} mAh")
        print(f"Tension                      : {voltage:.2f} V")
        print(f"Courant                      : {current:.2f} A")
        if current < -0.10:
            current_direction = "charge"
        elif current > 0.10:
            current_direction = "decharge"
        else:
            current_direction = "repos / courant faible"
        print(f"Sens du courant              : {current_direction}")
        print(f"Puissance instantanee        : {voltage * current:.1f} W")
        print(f"Temperatures BMS             : {temp_1} C / {temp_2} C")
    if bms_info:
        factory_capacity = _u16(bms_info, 18)
        remaining = _u16(bms) if bms else 0
        health = remaining / factory_capacity * 100 if factory_capacity else 0
        print(f"Capacite usine               : {factory_capacity} mAh")
        print(f"Cycles de charge             : {_u16(bms_info, 22)}")
        if remaining and factory_capacity:
            print(f"Charge/capacite usine        : {health:.1f} %")
    if cells_raw:
        cells = [_u16(cells_raw, offset) / 1000 for offset in range(0, 20, 2)]
        active_cells = [value for value in cells if value > 0]
        print("Cellules                     : " + ", ".join(f"{v:.3f} V" for v in active_cells))
        if active_cells:
            delta = (max(active_cells) - min(active_cells)) * 1000
            print(
                f"Cellule min/max/delta         : {min(active_cells):.3f} V / "
                f"{max(active_cells):.3f} V / {delta:.0f} mV"
            )

    runtime = values["runtime"]
    trip = values["trip"]
    range_data = values["range"]
    speed_data = values["speed"]
    print("\n=== CONDUITE ET ETAT ===")
    if runtime:
        print(f"Vitesse actuelle             : {_s16(runtime, 10) / 1000:.2f} km/h")
        print(f"Vitesse moyenne              : {_u16(runtime, 12) / 1000:.2f} km/h")
        print(f"Odometre                     : {_u32(runtime, 14) / 1000:.3f} km")
        print(f"Distance trajet              : {_s16(runtime, 18)} m")
        print(f"Temps depuis allumage        : {_u16(runtime, 20)} s")
        print(f"Temperature controleur       : {_s16(runtime, 22) / 10:.1f} C")
        print(f"Code erreur                  : {_u16(runtime, 0)}")
        print(f"Code avertissement           : {_u16(runtime, 2)}")
        print(f"Drapeaux d'etat              : 0x{_u16(runtime, 4):04X}")
        print(f"Mode de fonctionnement       : 0x{_u16(runtime, 6):04X}")
        print(f"Verrou logiciel              : {'actif' if _u16(runtime, 4) & 0x02 else 'inactif'}")
    if speed_data:
        print(f"Vitesse (registre dedie)     : {_s16(speed_data) / 1000:.2f} km/h")
    if trip:
        print(f"Trajet courant               : {_u16(trip, 2)} m en {_u16(trip)} s")
    if range_data:
        print(f"Autonomie estimee            : {_u16(range_data) / 100:.2f} km")

    kers_names = {0: "faible", 1: "moyen", 2: "fort"}
    tail_names = {0: "eteint", 1: "freinage", 2: "toujours allume"}
    kers = values["kers"]
    cruise = values["cruise"]
    tail = values["tail"]
    print("\n=== REGLAGES ===")
    if kers:
        value = _u16(kers)
        print(f"Freinage regeneratif         : {kers_names.get(value, f'inconnu ({value})')}")
    if cruise:
        print(f"Regulateur de vitesse        : {'active' if _u16(cruise) else 'desactive'}")
    if tail:
        value = _u16(tail)
        print(f"Feu arriere                  : {tail_names.get(value, f'inconnu ({value})')}")


async def _apply_commands(
    uart: mi_auth.MiUartClient,
    args: argparse.Namespace,
    snapshot: dict[str, bytes | None],
) -> None:
    changed = False
    if args.lock is not None:
        register = 0x70 if args.lock else 0x71
        await uart.write_register(0x20, register, b"\x01\x00")
        print(f"[commande] {'Verrouillage' if args.lock else 'Deverrouillage'} envoye.")
        changed = True

    if args.tail_light is not None:
        tail_values = {"off": 0, "brake": 1, "always": 2}
        await uart.write_register(
            0x20, 0x7D, tail_values[args.tail_light].to_bytes(2, "little")
        )
        print(f"[commande] Mode du feu arriere: {args.tail_light}.")
        snapshot["tail"] = tail_values[args.tail_light].to_bytes(2, "little")
        changed = True

    if args.cruise is not None:
        value = 1 if args.cruise == "on" else 0
        await uart.write_register(0x20, 0x7C, value.to_bytes(2, "little"))
        print(f"[commande] Regulateur: {args.cruise}.")
        changed = True

    if args.kers is not None:
        kers_values = {"weak": 0, "medium": 1, "strong": 2}
        await uart.write_register(
            0x20, 0x7B, kers_values[args.kers].to_bytes(2, "little")
        )
        print(f"[commande] Freinage regeneratif: {args.kers}.")
        changed = True

    if args.flash_tail_light:
        current_tail = snapshot["tail"]
        if current_tail is None:
            raise mi_auth.MiAuthError(
                "Impossible de restaurer le feu: son etat initial est inconnu"
            )
        restore = _u16(current_tail)
        for _ in range(args.flash_tail_light):
            await uart.write_register(0x20, 0x7D, b"\x02\x00")
            await asyncio.sleep(0.35)
            await uart.write_register(0x20, 0x7D, b"\x00\x00")
            await asyncio.sleep(0.35)
        await uart.write_register(0x20, 0x7D, restore.to_bytes(2, "little"))
        print(
            f"[commande] Feu arriere clignote {args.flash_tail_light} fois, "
            "etat initial restaure."
        )
        changed = True

    if changed:
        await asyncio.sleep(0.2)
        print("\n=== VERIFICATION DES REGLAGES ===")
        runtime = await _read_optional(uart, "Verrou", 0x20, 0xB0, 32)
        cruise = await _read_optional(uart, "Regulateur", 0x20, 0x7C, 2)
        tail = await _read_optional(uart, "Feu arriere", 0x20, 0x7D, 2)
        kers = await _read_optional(uart, "Freinage regeneratif", 0x20, 0x7B, 2)
        if runtime:
            print(f"Verrou : {'actif' if _u16(runtime, 4) & 0x02 else 'inactif'}")
        if cruise:
            print(f"Regulateur : {'active' if _u16(cruise) else 'desactive'}")
        if tail:
            print(f"Feu arriere : {_u16(tail)} (0=off, 1=freinage, 2=allume)")
        if kers:
            print(f"KERS : {_u16(kers)} (0=faible, 1=moyen, 2=fort)")


async def _test_mi_auth(
    client: protocol.DownGScooterClient,
    *,
    address: str,
    name: str,
    args: argparse.Namespace,
) -> None:
    register = args.register
    timeout = args.read_timeout
    token = None if register else _load_token(address)
    if token is None and not register:
        if await _probe_mi_auth_transport(client, timeout):
            print(
                "[succes] Transport MiAuth FE95 valide. Pour enregistrer ce "
                "PC et tester l'authentification complete, relancez avec "
                "--register."
            )
            return
        raise protocol.ScooterProtocolError(
            "The scooter did not answer the MiAuth GET_INFO request"
        )

    if register:
        print(
            "[miauth] Enregistrement demande. Ceci peut remplacer "
            "l'association utilisee par l'application DownG."
        )
        token = await _register_mi_auth(client, timeout=timeout)
        path = _save_token(address, name, token)
        print(f"[miauth] Jeton enregistre localement dans {path}")
    else:
        print(f"[miauth] Jeton local charge depuis {_token_path(address)}")

    bleak_client = _connected_bleak_client(client)
    print("[miauth] Authentification avec le jeton...")
    keys = await mi_auth.login(bleak_client, token, timeout=timeout)
    print("[miauth] Authentification reussie.")

    uart = mi_auth.MiUartClient(
        bleak_client,
        mi_auth.MiUartSession(keys),
        timeout=timeout,
    )
    await uart.start()
    try:
        snapshot = await _read_full_snapshot(uart)
        await _apply_commands(uart, args, snapshot)
    finally:
        await uart.stop()

    print(
        "\n[commandes] Disponibles: --lock, --unlock, "
        "--tail-light off|brake|always, --flash-tail-light [N], "
        "--cruise on|off, --kers weak|medium|strong"
    )


async def _diagnose(args: argparse.Namespace) -> int:
    found = await _scan(args.address, args.name, args.scan_timeout)
    if found is None:
        print(
            "[echec] Trottinette introuvable avec le Bluetooth local. "
            "Verifiez qu'elle est allumee, proche et deconnectee du telephone."
        )
        return 2

    device, advertisement = found
    _print_advertisement(device, advertisement)
    protocol_mode = _downg_protocol_mode(advertisement)
    if protocol_mode is not None:
        print(f"[scan] Selecteur de protocole DownG: 0x{protocol_mode:02X}")
    if args.scan_only:
        return 0

    scooter_name = device.name or advertisement.local_name or args.name
    if scooter_name is None and not args.connect_only:
        print(
            "[echec] Windows n'a pas fourni le nom BLE. Relancez avec le nom "
            "exact, par exemple --name MIScooter0128."
        )
        return 4

    client = protocol.DownGScooterClient(
        device,
        scooter_name=scooter_name or device.address,
        protocol=(const.PROTOCOL_MIAUTH if protocol_mode == 2 else const.PROTOCOL_PLAIN),
    )
    total_started = monotonic()
    try:
        started = monotonic()
        print("[connexion] Ouverture GATT directe depuis le PC...")
        await client.connect()
        print(f"[connexion] OK en {monotonic() - started:.2f}s")
        if args.connect_only:
            return 0

        if protocol_mode == 2:
            print(
                "[protocole] Mode 0x02 detecte: authentification Xiaomi FE95, "
                "pas 5AA5."
            )
            await _test_mi_auth(
                client,
                address=device.address,
                name=scooter_name,
                args=args,
            )
            print(f"[succes] Test termine en {monotonic() - total_started:.2f}s")
            return 0

        started = monotonic()
        print("[protocole] Test Xiaomi 55AA...")
        try:
            await client.probe(timeout=args.probe_timeout)
        except protocol.ScooterProtocolError as plain_error:
            print(f"[protocole] 55AA sans reponse: {plain_error}")
            raise
        else:
            selected_protocol = "55AA clair"
            print(f"[protocole] 55AA valide en {monotonic() - started:.2f}s")

        print("[telemetrie] Lecture des donnees...")
        data = await client.read_telemetry()
        print(f"[succes] Protocole: {selected_protocol}")
        print(f"[succes] Batterie: {data.battery_percent}%")
        print(f"[succes] Tension: {data.battery_voltage_v} V")
        print(f"[succes] Vitesse: {data.speed_kmh} km/h")
        print(f"[succes] Odometre: {data.odometer_km} km")
        print(f"[succes] Numero de serie: {data.scooter_serial or 'indisponible'}")
        print(f"[succes] Test complet en {monotonic() - total_started:.2f}s")
        return 0
    except (
        BleakError,
        mi_auth.MiAuthError,
        protocol.ScooterProtocolError,
        TimeoutError,
    ) as err:
        print(f"[echec] {type(err).__name__}: {err}")
        if args.debug:
            logging.exception("Diagnostic scooter failed")
        return 3
    finally:
        await client.disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Teste une trottinette Xiaomi directement avec le Bluetooth du PC, "
            "sans Home Assistant ni proxy ESPHome."
        )
    )
    parser.add_argument("--address", help="Adresse BLE, par exemple D4:5D:A0:18:13:8D")
    parser.add_argument(
        "--name",
        help=(
            "Nom BLE exact utilise pour l'authentification, par exemple "
            "MIScooter0128"
        ),
    )
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    parser.add_argument("--probe-timeout", type=float, default=1.5)
    parser.add_argument("--read-timeout", type=float, default=4.0)
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--connect-only", action="store_true")
    parser.add_argument(
        "--register",
        action="store_true",
        help=(
            "Enregistre ce PC aupres de la trottinette. Peut remplacer "
            "l'association de l'application mobile."
        ),
    )
    lock_group = parser.add_mutually_exclusive_group()
    lock_group.add_argument("--lock", dest="lock", action="store_true")
    lock_group.add_argument("--unlock", dest="lock", action="store_false")
    parser.set_defaults(lock=None)
    parser.add_argument(
        "--tail-light",
        choices=("off", "brake", "always"),
        help="Mode du feu arriere.",
    )
    parser.add_argument(
        "--flash-tail-light",
        nargs="?",
        const=3,
        default=0,
        type=int,
        metavar="N",
        help="Fait clignoter le feu N fois (3 par defaut), puis restaure son etat.",
    )
    parser.add_argument("--cruise", choices=("on", "off"))
    parser.add_argument("--kers", choices=("weak", "medium", "strong"))
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_diagnose(args))
    except KeyboardInterrupt:
        print("\n[annule] Diagnostic interrompu.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
