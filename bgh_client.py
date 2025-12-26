"""BGH Smart AC UDP client."""
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


class BGHClient:
    """BGH Smart AC UDP client - Broadcast listener."""

    def __init__(self, host: str) -> None:
        """Initialize the client."""
        self.host = host
        self._send_sock: socket.socket | None = None
        self._recv_sock: socket.socket | None = None
        self._listener_task: asyncio.Task | None = None
        self._current_mode = 0
        self._current_fan = 1
        self._last_status: dict[str, Any] = {}
        self._status_callback: Callable[[dict], None] | None = None
        self._device_id: str | None = None  # Device ID extraído de broadcasts

    async def async_connect(self) -> bool:
        """Connect to the AC unit and start listening for broadcasts."""
        try:
            _LOGGER.info("=== BGH Client connecting to %s ===", self.host)
            
            # Create sockets synchronously to avoid async issues
            try:
                self._recv_sock = self._create_recv_socket()
                _LOGGER.info("✓ Broadcast receive socket created")
            except Exception as e:
                _LOGGER.error("Failed to create receive socket: %s", e)
                return False
            
            try:
                self._send_sock = self._create_send_socket()
                _LOGGER.info("✓ Send socket created")
            except Exception as e:
                _LOGGER.error("Failed to create send socket: %s", e)
                if self._recv_sock:
                    self._recv_sock.close()
                return False
            
            # Start listener task
            _LOGGER.info("Starting broadcast listener task...")
            self._listener_task = asyncio.create_task(self._broadcast_listener())
            _LOGGER.info("✓ Broadcast listener task started")
            
            _LOGGER.info("BGH Client connected for %s", self.host)
            
            # Send initial status query to trigger a broadcast
            _LOGGER.info("Sending initial status request...")
            await self.async_request_status()
            _LOGGER.info("✓ Connection complete")
            
            return True
        except Exception as err:
            _LOGGER.error("Failed to connect to %s: %s", self.host, err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return False

    def _create_send_socket(self) -> socket.socket:
        """Create UDP send socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(5)
        # Don't bind - system assigns random source port
        _LOGGER.info("Send socket created")
        return sock

    def _create_recv_socket(self) -> socket.socket:
        """Create UDP broadcast receive socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        sock.bind(("", UDP_RECV_PORT))
        sock.setblocking(False)
        _LOGGER.info("Broadcast receive socket bound to port %d", UDP_RECV_PORT)
        return sock

    async def _broadcast_listener(self) -> None:
        """Listen for UDP broadcasts from the AC unit."""
        _LOGGER.info("🎧 Broadcast listener started for %s", self.host)
        _LOGGER.info("   Listening on port %d for broadcasts from %s", UDP_RECV_PORT, self.host)
        
        broadcast_timeout = 0
        
        while True:
            try:
                if not self._recv_sock:
                    _LOGGER.warning("Receive socket is None, stopping listener")
                    break
                    
                loop = asyncio.get_event_loop()
                
                # Try to receive with timeout
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(self._recv_sock, 1024),
                        timeout=15.0  # 15 second timeout
                    )
                    
                    _LOGGER.debug("📡 Received UDP packet from %s: %d bytes", addr, len(data))
                    
                    # Reset timeout counter on successful receive
                    broadcast_timeout = 0
                    
                    # Only process broadcasts from our AC unit
                    if addr[0] == self.host:
                        _LOGGER.info("✅ Broadcast from AC %s: %d bytes", addr, len(data))
                        
                        # Extract device ID from first broadcast (bytes 1-6, after initial 0x00)
                        if not self._device_id and len(data) >= 7:
                            self._device_id = data[1:7].hex()
                            _LOGGER.warning(">>> DEVICE ID EXTRACTED <<<")
                            _LOGGER.warning("    Raw broadcast: %s", data.hex())
                            _LOGGER.warning("    Device ID: %s", self._device_id)
                        
                        status = self._parse_status(data)
                        
                        if status:
                            self._last_status = status
                            _LOGGER.info("   Parsed: mode=%s, fan=%s, temp=%.1f°C", 
                                       status.get('mode'), status.get('fan_speed'), 
                                       status.get('current_temperature', 0))
                            if self._status_callback:
                                self._status_callback(status)
                    else:
                        _LOGGER.debug("   Ignoring broadcast from %s (not our AC)", addr[0])
                        
                except asyncio.TimeoutError:
                    # No broadcast received in 15 seconds
                    broadcast_timeout += 1
                    
                    if broadcast_timeout == 1:
                        _LOGGER.warning("⚠️  No broadcasts received from %s (network issue?)", self.host)
                        _LOGGER.warning("   Switching to polling mode...")
                    
                    # Request status when no broadcasts arrive
                    _LOGGER.debug("Polling: Requesting status from %s", self.host)
                    await self.async_request_status()
                    
                    # Wait a bit for the AC to respond with broadcast
                    await asyncio.sleep(1)
                            
            except asyncio.CancelledError:
                _LOGGER.info("Broadcast listener stopped for %s", self.host)
                break
            except Exception as err:
                _LOGGER.error("Error in broadcast listener: %s", err)
                import traceback
                _LOGGER.error("Traceback: %s", traceback.format_exc())
                await asyncio.sleep(1)

    async def async_request_status(self) -> None:
        """Request status update (triggers a broadcast from the AC)."""
        try:
            # Status command doesn't need device ID
            CMD_STATUS = "00000000000000accf23aa3190590001e4"
            command = bytes.fromhex(CMD_STATUS)
            await self._send_command(command)
            _LOGGER.debug("Status request sent to %s", self.host)
        except Exception as err:
            _LOGGER.error("Failed to request status: %s", err)

    async def async_get_status(self) -> dict[str, Any] | None:
        """Get current status (returns last received broadcast)."""
        # If we don't have status yet, request one and wait a bit
        if not self._last_status:
            await self.async_request_status()
            await asyncio.sleep(1)
        
        return self._last_status if self._last_status else None

    async def async_set_mode(
        self,
        mode: int,
        fan_speed: int | None = None,
    ) -> bool:
        """Set AC mode and fan speed."""
        try:
            # Wait for device ID to be extracted from broadcasts
            if not self._device_id:
                _LOGGER.warning("Device ID not yet extracted, waiting for broadcast...")
                await asyncio.sleep(2)
                if not self._device_id:
                    _LOGGER.error("Cannot send command without Device ID")
                    return False
            
            # Update current state
            self._current_mode = mode
            if fan_speed is not None:
                self._current_fan = fan_speed

            # Build control command with device ID
            # Format: 00000000000000[DEVICE_ID]f6000161[MODE][FAN]000080
            # Based on Node-RED: mode at byte 17, fan at byte 18
            cmd_base = f"00000000000000{self._device_id}f60001610402000080"
            command = bytearray(bytes.fromhex(cmd_base))
            command[17] = self._current_mode
            command[18] = self._current_fan

            _LOGGER.info("Sending mode command: mode=%d, fan=%d, device_id=%s",
                        self._current_mode, self._current_fan, self._device_id)
            await self._send_command(bytes(command))
            
            # Wait a bit for AC to process
            await asyncio.sleep(0.3)
            
            # Request status update (will trigger broadcast)
            await self.async_request_status()
            
            return True
        except Exception as err:
            _LOGGER.error("Failed to set mode on %s: %s", self.host, err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return False

    async def async_set_temperature(self, temperature: float) -> bool:
        """Set target temperature."""
        try:
            # Wait for device ID to be extracted from broadcasts
            if not self._device_id:
                _LOGGER.warning("Device ID not yet extracted, waiting for broadcast...")
                await asyncio.sleep(2)
                if not self._device_id:
                    _LOGGER.error("Cannot send command without Device ID")
                    return False

            # Build temperature command with device ID
            # Format: 00000000000000[DEVICE_ID]8100016101[MODE][FAN]00[TEMP_LO][TEMP_HI]
            # Byte 13 = 0x81 (temperature command)
            # Bytes 17-18 = mode and fan (current values)
            # Bytes 20-21 = temperature * 100 in little-endian
            cmd_base = f"00000000000000{self._device_id}810001610100000000"
            command = bytearray(bytes.fromhex(cmd_base))
            command[17] = self._current_mode
            command[18] = self._current_fan
            
            # Temperature as 16-bit little-endian, multiplied by 100
            temp_raw = int(temperature * 100)
            command[20] = temp_raw & 0xFF         # Low byte
            command[21] = (temp_raw >> 8) & 0xFF  # High byte

            _LOGGER.info("Sending temperature command: temp=%.1f°C, mode=%d, fan=%d, device_id=%s",
                        temperature, self._current_mode, self._current_fan, self._device_id)
            _LOGGER.debug("Temperature command hex: %s", bytes(command).hex())
            await self._send_command(bytes(command))
            
            # Wait a bit for AC to process
            await asyncio.sleep(0.3)
            
            # Request status update (will trigger broadcast)
            await self.async_request_status()
            
            return True
        except Exception as err:
            _LOGGER.error("Failed to set temperature on %s: %s", self.host, err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return False

    async def _send_command(self, command: bytes) -> None:
        """Send UDP command - creates new socket each time like working test."""
        _LOGGER.debug("Sending %d bytes to %s:%d", len(command), self.host, UDP_SEND_PORT)
        
        # Create new socket, send, close - just like the working test script
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(command, (self.host, UDP_SEND_PORT))
            _LOGGER.debug("Sent command: %s", command.hex())
        finally:
            sock.close()

    def _parse_status(self, data: bytes) -> dict[str, Any]:
        """Parse status response."""
        if len(data) < 22:
            _LOGGER.warning("Invalid status data length: %d", len(data))
            return {}

        # Extract data according to Node-RED flow
        mode = data[18]
        fan_speed = data[19]
        
        # Temperature is in bytes 21-22 (little-endian, divided by 100)
        temp_raw = struct.unpack("<H", data[21:23])[0]
        current_temp = temp_raw / 100.0
        
        # Setpoint is in bytes 23-24
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

        # Update internal state
        self._current_mode = mode
        self._current_fan = fan_speed

        _LOGGER.debug("Parsed status from %s: %s", self.host, status)
        return status

    async def async_close(self) -> None:
        """Close the connection."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
            
        if self._send_sock:
            self._send_sock.close()
            self._send_sock = None
            
        if self._recv_sock:
            self._recv_sock.close()
            self._recv_sock = None