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
    UnitOfSpeed,
    UnitOfTemperature,
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


SENSORS: tuple[DownGSensorDescription, ...] = (
    DownGSensorDescription(
        key="battery_percent",
        translation_key="battery_percent",
        value_fn=lambda data: data.battery_percent,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_remaining",
        translation_key="battery_remaining",
        value_fn=lambda data: data.battery_remaining_mah,
        native_unit_of_measurement="mAh",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        value_fn=lambda data: data.battery_voltage_v,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_current",
        translation_key="battery_current",
        value_fn=lambda data: data.battery_current_a,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_temperature",
        translation_key="battery_temperature",
        value_fn=lambda data: data.battery_temperature_c,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_cell_min",
        translation_key="battery_cell_min",
        value_fn=lambda data: data.battery_cell_min_v,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_cell_max",
        translation_key="battery_cell_max",
        value_fn=lambda data: data.battery_cell_max_v,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_cell_delta",
        translation_key="battery_cell_delta",
        value_fn=lambda data: data.battery_cell_delta_mv,
        native_unit_of_measurement="mV",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_factory_capacity",
        translation_key="battery_factory_capacity",
        value_fn=lambda data: data.battery_factory_capacity_mah,
        native_unit_of_measurement="mAh",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="battery_charge_cycles",
        translation_key="battery_charge_cycles",
        value_fn=lambda data: data.battery_charge_cycles,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    DownGSensorDescription(
        key="speed",
        translation_key="speed",
        value_fn=lambda data: data.speed_kmh,
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    DownGSensorDescription(
        key="odometer",
        translation_key="odometer",
        value_fn=lambda data: data.odometer_km,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    DownGSensorDescription(
        key="scooter_serial",
        translation_key="scooter_serial",
        value_fn=lambda data: data.scooter_serial,
        state_class=None,
    ),
    DownGSensorDescription(
        key="battery_serial",
        translation_key="battery_serial",
        value_fn=lambda data: data.battery_serial,
        state_class=None,
    ),
    DownGSensorDescription(
        key="battery_manufacture_date",
        translation_key="battery_manufacture_date",
        value_fn=lambda data: data.battery_manufacture_date,
        state_class=None,
    ),
    DownGSensorDescription(
        key="drv_version",
        translation_key="drv_version",
        value_fn=lambda data: data.drv_version,
        state_class=None,
    ),
    DownGSensorDescription(
        key="bms_version",
        translation_key="bms_version",
        value_fn=lambda data: data.bms_version,
        state_class=None,
    ),
    DownGSensorDescription(
        key="error_code",
        translation_key="error_code",
        value_fn=lambda data: data.error_code,
        state_class=None,
    ),
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
    """Scooter telemetry sensor."""

    entity_description: DownGSensorDescription

    def __init__(
        self,
        coordinator: DownGScooterCoordinator,
        description: DownGSensorDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._update_value()

    def _update_value(self) -> None:
        self._attr_native_value = self.entity_description.value_fn(
            self.coordinator.data
        )

    def _handle_coordinator_update(self) -> None:
        """Refresh the cached state from coordinator data."""
        self._update_value()
        super()._handle_coordinator_update()
