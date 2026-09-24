"""Sensors for DownG Scooter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPower,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DownGScooterCoordinator
from .entity import DownGScooterEntity
from .protocol import ScooterData


@dataclass(frozen=True, kw_only=True)
class DownGSensorDescription(SensorEntityDescription):
    """Describe a scooter sensor."""

    value_fn: Callable[[ScooterData], float | int | str | None]


def _sensor(
    key: str,
    value_fn: Callable[[ScooterData], float | int | str | None],
    *,
    device_class: SensorDeviceClass | None = None,
    native_unit_of_measurement: str | None = None,
    state_class: SensorStateClass | None = None,
) -> DownGSensorDescription:
    return DownGSensorDescription(
        key=key,
        translation_key=key,
        value_fn=value_fn,
        device_class=device_class,
        native_unit_of_measurement=native_unit_of_measurement,
        state_class=state_class,
    )


SENSORS: tuple[DownGSensorDescription, ...] = (
    _sensor("battery_percent", lambda d: d.battery_percent, device_class=SensorDeviceClass.BATTERY, native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_remaining", lambda d: d.battery_remaining_mah, native_unit_of_measurement="mAh", state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_voltage", lambda d: d.battery_voltage_v, device_class=SensorDeviceClass.VOLTAGE, native_unit_of_measurement=UnitOfElectricPotential.VOLT, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_current", lambda d: d.battery_current_a, device_class=SensorDeviceClass.CURRENT, native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_power", lambda d: d.battery_power_w, device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_temperature", lambda d: d.battery_temperature_c, device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_temperature_1", lambda d: d.battery_temperature_1_c, device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_temperature_2", lambda d: d.battery_temperature_2_c, device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_status", lambda d: f"0x{d.battery_status:04X}" if d.battery_status is not None else None),
    _sensor("battery_cell_min", lambda d: d.battery_cell_min_v, device_class=SensorDeviceClass.VOLTAGE, native_unit_of_measurement=UnitOfElectricPotential.VOLT, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_cell_max", lambda d: d.battery_cell_max_v, device_class=SensorDeviceClass.VOLTAGE, native_unit_of_measurement=UnitOfElectricPotential.VOLT, state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_cell_delta", lambda d: d.battery_cell_delta_mv, native_unit_of_measurement="mV", state_class=SensorStateClass.MEASUREMENT),
    *tuple(
        _sensor(
            f"battery_cell_{index}",
            lambda d, cell=index: d.battery_cell_voltages_v[cell - 1] if len(d.battery_cell_voltages_v) >= cell else None,
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
        )
        for index in range(1, 11)
    ),
    _sensor("battery_factory_capacity", lambda d: d.battery_factory_capacity_mah, native_unit_of_measurement="mAh", state_class=SensorStateClass.MEASUREMENT),
    _sensor("battery_charge_cycles", lambda d: d.battery_charge_cycles, state_class=SensorStateClass.TOTAL_INCREASING),
    _sensor("speed", lambda d: d.speed_kmh, device_class=SensorDeviceClass.SPEED, native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR, state_class=SensorStateClass.MEASUREMENT),
    _sensor("average_speed", lambda d: d.average_speed_kmh, device_class=SensorDeviceClass.SPEED, native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR, state_class=SensorStateClass.MEASUREMENT),
    _sensor("odometer", lambda d: d.odometer_km, device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS, state_class=SensorStateClass.TOTAL_INCREASING),
    _sensor("trip_distance", lambda d: d.trip_distance_m, device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.METERS, state_class=SensorStateClass.TOTAL),
    _sensor("trip_time", lambda d: d.trip_time_s, device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.SECONDS, state_class=SensorStateClass.MEASUREMENT),
    _sensor("uptime", lambda d: d.uptime_s, device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.SECONDS, state_class=SensorStateClass.MEASUREMENT),
    _sensor("estimated_range", lambda d: d.estimated_range_km, device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS, state_class=SensorStateClass.MEASUREMENT),
    _sensor("controller_temperature", lambda d: d.controller_temperature_c, device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT),
    _sensor("scooter_serial", lambda d: d.scooter_serial),
    _sensor("battery_serial", lambda d: d.battery_serial),
    _sensor("battery_manufacture_date", lambda d: d.battery_manufacture_date),
    _sensor("firmware_version", lambda d: d.firmware_version),
    _sensor("drv_version", lambda d: d.drv_version),
    _sensor("bms_version", lambda d: d.bms_version),
    _sensor("bms_version_extended", lambda d: d.bms_version_extended),
    _sensor("error_code", lambda d: d.error_code),
    _sensor("warning_code", lambda d: d.warning_code),
    _sensor("state_flags", lambda d: f"0x{d.state_flags:04X}" if d.state_flags is not None else None),
    _sensor("work_mode", lambda d: f"0x{d.work_mode:04X}" if d.work_mode is not None else None),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up scooter sensors."""
    coordinator: DownGScooterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DownGScooterSensor(coordinator, description) for description in SENSORS
    )


class DownGScooterSensor(DownGScooterEntity, SensorEntity):
    """One scooter telemetry sensor."""

    entity_description: DownGSensorDescription

    def __init__(
        self, coordinator: DownGScooterCoordinator, description: DownGSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._update_value()

    def _update_value(self) -> None:
        self._attr_native_value = self.entity_description.value_fn(self.coordinator.data)

    def _handle_coordinator_update(self) -> None:
        self._update_value()
        super()._handle_coordinator_update()
