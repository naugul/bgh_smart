"""BGH Smart AC UDP client - Polling mode."""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Any, Callable

from .const import (
    MODES,
    UDP_RECV_PORT,
    UDP_SEND_PORT,
    UDP_SOURCE_PORT,
)

_LOGGER = logging.getLogger(__name__)

POLLING_INTERVAL = 60  # Poll every 60 seconds


class BGHClient:
    """BGH Smart AC UDP client - Polling mode."""

    def __init__(self, host: str) -> None:
        """Initialize the client."""
        self.host = host
        self._send_sock: socket.socket | None = None
        self._recv_sock: socket.socket | None = None
        self._polling_task: asyncio.Task | None = None
        self._current_mode = 0
        self._current_fan = 1
        self._last_status: dict[str, Any] = {}
        self._status_callback: Callable[[dict], None] | None = None
        self._device_id: str | None = None

    async def async_connect(self) -> bool:
        """Connect to the AC unit and start polling."""
        try:
            _LOGGER.info("=== BGH Client connecting to %s (polling mode) ===", self.host)
            
            # Create receive socket for polling responses
            try:
                self._recv_sock = self._create_recv_socket()
                _LOGGER.info("✓ Receive socket created")
            except Exception as e:
                _LOGGER.error("Failed to create receive socket: %s", e)
                return False
            
            # Start polling task
            _LOGGER.info("Starting polling task (interval: %ds)...", POLLING_INTERVAL)
            self._polling_task = asyncio.create_task(self._polling_loop())
            _LOGGER.info("✓ Polling task started")
            
            _LOGGER.info("BGH Client connected for %s", self.host)
            
            # Do initial poll
            _LOGGER.info("Performing initial status poll...")
            await self._poll_status()
            _LOGGER.info("✓ Connection complete")
            
            return True
        except Exception as err:
            _LOGGER.error("Failed to connect to %s: %s", self.host, err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return False

    def _create_recv_socket(self) -> socket.socket:
        """Create UDP broadcast receive socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        sock.bind(("", UDP_RECV_PORT))
        sock.setblocking(False)
        _LOGGER.info("Receive socket bound to port %d", UDP_RECV_PORT)
        return sock

    async def _polling_loop(self) -> None:
        """Poll for status updates at regular intervals."""
        _LOGGER.info("🔄 Polling loop started for %s (every %ds)", self.host, POLLING_INTERVAL)
        
        while True:
            try:
                await asyncio.sleep(POLLING_INTERVAL)
                await self._poll_status()
                    
            except asyncio.CancelledError:
                _LOGGER.info("Polling loop stopped for %s", self.host)
                break
            except Exception as err:
                _LOGGER.error("Error in polling loop: %s", err)
                import traceback
                _LOGGER.error("Traceback: %s", traceback.format_exc())

    async def _poll_status(self) -> None:
        """Poll for status by sending request and waiting for response."""
        try:
            # Send status request
            CMD_STATUS = "00000000000000accf23aa3190590001e4"
            command = bytes.fromhex(CMD_STATUS)
            await self._send_command(command)
            _LOGGER.debug("Status poll sent to %s", self.host)
            
            # Wait for response with timeout
            if not self._recv_sock:
                _LOGGER.warning("Receive socket is None")
                return
                
            loop = asyncio.get_event_loop()
            
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(self._recv_sock, 1024),
                    timeout=5.0
                )
                
                # Only process response from our AC unit
                if addr[0] == self.host:
                    _LOGGER.debug("📡 Poll response from %s: %d bytes", addr, len(data))
                    
                    # Extract device ID from first response if not set
                    if not self._device_id and len(data) >= 7:
                        self._device_id = data[1:7].hex()
                        _LOGGER.info("Device ID extracted: %s", self._device_id)
                    
                    status = self._parse_status(data)
                    
                    if status:
                        self._last_status = status
                        _LOGGER.info("Status updated: mode=%s, fan=%s, temp=%.1f°C", 
                                   status.get('mode'), status.get('fan_speed'), 
                                   status.get('current_temperature', 0))
                        if self._status_callback:
                            self._status_callback(status)
                            
            except asyncio.TimeoutError:
                _LOGGER.warning("No response to status poll from %s", self.host)
                    
        except Exception as err:
            _LOGGER.error("Failed to poll status: %s", err)

    async def async_request_status(self) -> None:
        """Request status update immediately (outside regular polling)."""
        await self._poll_status()

    async def async_get_status(self) -> dict[str, Any] | None:
        """Get current status."""
        if not self._last_status:
            await self._poll_status()
        
        return self._last_status if self._last_status else None

    async def async_set_mode(
        self,
        mode: int,
        fan_speed: int | None = None,
    ) -> bool:
        """Set AC mode and fan speed."""
        try:
            if not self._device_id:
                _LOGGER.warning("Device ID not yet extracted, waiting...")
                await asyncio.sleep(2)
                if not self._device_id:
                    _LOGGER.error("Cannot send command without Device ID")
                    return False
            
            self._current_mode = mode
            if fan_speed is not None:
                self._current_fan = fan_speed

            cmd_base = f"00000000000000{self._device_id}f60001610402000080"
            command = bytearray(bytes.fromhex(cmd_base))
            command[17] = self._current_mode
            command[18] = self._current_fan

            _LOGGER.info("Sending mode command: mode=%d, fan=%d", 
                        self._current_mode, self._current_fan)
            await self._send_command(bytes(command))
            
            await asyncio.sleep(0.3)
            await self._poll_status()
            
            return True
        except Exception as err:
            _LOGGER.error("Failed to set mode on %s: %s", self.host, err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return False

    async def async_set_temperature(self, temperature: float) -> bool:
        """Set target temperature."""
        try:
            if not self._device_id:
                _LOGGER.warning("Device ID not yet extracted, waiting...")
                await asyncio.sleep(2)
                if not self._device_id:
                    _LOGGER.error("Cannot send command without Device ID")
                    return False

            cmd_base = f"00000000000000{self._device_id}810001610100000000"
            command = bytearray(bytes.fromhex(cmd_base))
            command[17] = self._current_mode
            command[18] = self._current_fan
            
            temp_raw = int(temperature * 100)
            command[20] = temp_raw & 0xFF
            command[21] = (temp_raw >> 8) & 0xFF

            _LOGGER.info("Sending temperature command: temp=%.1f°C", temperature)
            await self._send_command(bytes(command))
            
            await asyncio.sleep(0.3)
            await self._poll_status()
            
            return True
        except Exception as err:
            _LOGGER.error("Failed to set temperature on %s: %s", self.host, err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return False

    async def _send_command(self, command: bytes) -> None:
        """Send UDP command."""
        _LOGGER.debug("Sending %d bytes to %s:%d", len(command), self.host, UDP_SEND_PORT)
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(command, (self.host, UDP_SEND_PORT))
            _LOGGER.debug("Sent command: %s", command.hex())
        finally:
            sock.close()

    def _parse_status(self, data: bytes) -> dict[str, Any]:
        """Parse status response."""
        if len(data) < 25:
            _LOGGER.warning("Invalid status data length: %d", len(data))
            return {}

        mode = data[18]
        fan_speed = data[19]
        
        temp_raw = struct.unpack("<H", data[21:23])[0]
        current_temp = temp_raw / 100.0
        
        setpoint_raw = struct.unpack("<H", data[23:25])[0]
        target_temp = setpoint_raw / 100.0

        status = {
            "mode": MODES.get(mode, "unknown"),
            "mode_raw": mode,
            "fan_speed": fan_speed,
            "current_temperature": current_temp,
            "target_temperature": target_temp,
            "is_on": mode != 0,
        }

        self._current_mode = mode
        self._current_fan = fan_speed

        _LOGGER.debug("Parsed status from %s: %s", self.host, status)
        return status

    async def async_close(self) -> None:
        """Close the connection."""
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
            
        if self._recv_sock:
            self._recv_sock.close()
            self._recv_sock = None