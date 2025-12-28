"""Climate platform for BGH Smart Control."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, CONF_NAME, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FAN_MODES,
    FAN_MODES_REVERSE,
    MAX_TEMP,
    MIN_TEMP,
    MODES_REVERSE,
)
from .coordinator import BGHDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Map BGH modes to HA HVAC modes
HVAC_MODE_MAP = {
    "off": HVACMode.OFF,
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "dry": HVACMode.DRY,
    "fan_only": HVACMode.FAN_ONLY,
    "auto": HVACMode.AUTO,
}

HVAC_MODE_REVERSE = {v: k for k, v in HVAC_MODE_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BGH climate platform."""
    coordinator: BGHDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities(
        [BGHClimate(coordinator, entry)],
        update_before_add=True,
    )


class BGHClimate(CoordinatorEntity[BGHDataUpdateCoordinator], ClimateEntity):
    """Representation of a BGH Smart AC unit."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 1.0
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
    )
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
    ]
    _attr_fan_modes = list(FAN_MODES.values())

    def __init__(
        self,
        coordinator: BGHDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data[CONF_NAME],
            "manufacturer": "BGH",
            "model": "Smart Control",
        }
        self._enable_turn_on_off_backwards_compatibility = False
        
        # Store last known good values
        self._last_valid_current_temp: float | None = None
        self._last_valid_target_temp: float | None = None
        self._last_valid_mode: str = "off"
        self._last_valid_fan: int = 1

    def _is_valid_temperature(self, temp: float | None) -> bool:
        """Check if temperature is within reasonable range."""
        if temp is None:
            return False
        # Reasonable AC temperature range: 10°C to 40°C
        # Adjust these limits based on your climate
        return 10.0 <= temp <= 40.0

    def _validate_and_store_data(self) -> None:
        """Validate coordinator data and store good values."""
        if not self.coordinator.data:
            _LOGGER.debug("No coordinator data available")
            return
        
        current_temp = self.coordinator.data.get("current_temperature")
        target_temp = self.coordinator.data.get("target_temperature")
        mode = self.coordinator.data.get("mode", "off")
        fan_speed = self.coordinator.data.get("fan_speed", 1)
        
        # Log the raw data for debugging
        _LOGGER.debug("Raw data: current=%.1f, target=%.1f, mode=%s, fan=%d",
                     current_temp if current_temp else 0,
                     target_temp if target_temp else 0,
                     mode, fan_speed)
        
        # Validate and store current temperature
        if self._is_valid_temperature(current_temp):
            if self._last_valid_current_temp is None or abs(current_temp - self._last_valid_current_temp) < 10:
                # Accept if first reading or change is less than 10°C
                self._last_valid_current_temp = current_temp
            else:
                _LOGGER.warning("Rejecting invalid current temp: %.1f (last valid: %.1f)",
                              current_temp, self._last_valid_current_temp)
        
        # Validate and store target temperature
        if self._is_valid_temperature(target_temp):
            if self._last_valid_target_temp is None or abs(target_temp - self._last_valid_target_temp) < 10:
                self._last_valid_target_temp = target_temp
            else:
                _LOGGER.warning("Rejecting invalid target temp: %.1f (last valid: %.1f)",
                              target_temp, self._last_valid_target_temp)
        
        # Store mode and fan (these are less likely to be corrupted)
        if mode in HVAC_MODE_MAP:
            self._last_valid_mode = mode
        
        if 0 <= fan_speed <= 5:  # Adjust range based on your AC
            self._last_valid_fan = fan_speed

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        self._validate_and_store_data()
        return self._last_valid_current_temp

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        self._validate_and_store_data()
        return self._last_valid_target_temp

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation mode."""
        self._validate_and_store_data()
        return HVAC_MODE_MAP.get(self._last_valid_mode, HVACMode.OFF)

    @property
    def fan_mode(self) -> str | None:
        """Return the fan setting."""
        self._validate_and_store_data()
        return FAN_MODES.get(self._last_valid_fan, "low")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            _LOGGER.error("No temperature provided")
            return
        
        await self.coordinator.async_set_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        bgh_mode = HVAC_MODE_REVERSE.get(hvac_mode)
        if bgh_mode is None:
            _LOGGER.error("Invalid HVAC mode: %s", hvac_mode)
            return

        mode_value = MODES_REVERSE.get(bgh_mode)
        if mode_value is None:
            _LOGGER.error("Cannot map mode: %s", bgh_mode)
            return

        # Keep current fan speed if available
        current_fan = self._last_valid_fan

        await self.coordinator.async_set_mode(mode_value, current_fan)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        fan_value = FAN_MODES_REVERSE.get(fan_mode)
        if fan_value is None:
            _LOGGER.error("Invalid fan mode: %s", fan_mode)
            return

        # Keep current mode
        current_mode = MODES_REVERSE.get(self._last_valid_mode, 0)

        await self.coordinator.async_set_mode(current_mode, fan_value)

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        # Default to cooling mode when turning on
        await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self.async_set_hvac_mode(HVACMode.OFF)