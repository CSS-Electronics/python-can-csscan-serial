import threading
import time
from collections import deque
from typing import Any, List, Optional, Tuple

import can
import serial
from serial.tools.list_ports import comports

from ._protocol import FLAG, MAX_FRAME_SIZE, decode, encode

# Serial read timeout. Bounds how long recv() can overshoot its timeout
_READ_TIMEOUT = 0.01

# USB (VID, PID) of supported devices
_USB_IDS = {
    (0x1CBE, 0x021A),  # CLX000
    (0x04D8, 0xEB18),  # CANmod
}


class CSSCANSerialBus(can.BusABC):
    """python-can bus for CSS Electronics devices with a USB serial interface (CLX000 and CANmod series).

    :param channel: Serial port, e.g. ``COM19`` or ``/dev/ttyACM0`` (any pyserial URL is accepted).
    :param receive_own_messages: Also receive the messages the device reports as transmitted by itself.
    """

    def __init__(
        self,
        channel: Any,
        can_filters: Optional[can.typechecking.CanFilters] = None,
        receive_own_messages: bool = False,
        **kwargs: object,
    ):
        if not channel:
            raise can.CanInitializationError("A channel (serial port) is required")

        try:
            self._serial = serial.serial_for_url(channel, timeout=_READ_TIMEOUT)
        except (serial.SerialException, ValueError) as e:
            raise can.CanInitializationError(f"Could not open {channel}: {e}") from e

        try:
            # Discard data received before the bus was opened
            self._serial.reset_input_buffer()
        except serial.SerialException as e:
            self._serial.close()
            raise can.CanInitializationError(f"Could not open {channel}: {e}") from e

        self._receive_own_messages = receive_own_messages
        self._buffer = bytearray()
        self._messages = deque()
        self._write_lock = threading.Lock()
        self.channel_info = f"CSSCAN serial on {channel}"

        super().__init__(channel, can_filters, **kwargs)

    def _recv_internal(self, timeout: Optional[float]) -> Tuple[Optional[can.Message], bool]:
        deadline = None if timeout is None else time.monotonic() + timeout

        try:
            while not self._messages:
                pending = self._serial.in_waiting
                if pending == 0 and deadline is not None and time.monotonic() >= deadline:
                    return None, False
                # Blocks for up to _READ_TIMEOUT if no data is pending
                data = self._serial.read(max(pending, 1))
                self._parse(data, time.time())
                if deadline is not None and time.monotonic() >= deadline:
                    break
        except serial.SerialException as e:
            raise can.CanOperationError(f"Could not read from {self.channel_info}: {e}") from e

        return (self._messages.popleft() if self._messages else None), False

    def _parse(self, data: bytes, timestamp: float) -> None:
        """Decode the frames completed by `data`. Messages are timestamped with the time the data was read."""
        if FLAG not in data:
            self._buffer += data
        else:
            # The last segment is either empty or an incomplete frame, kept for the next read. A segment preceding the
            # first flag (e.g. the tail of a frame when connecting mid-stream) fails the CRC check and is dropped.
            *frames, self._buffer = (self._buffer + data).split(bytes([FLAG]))
            for frame in frames:
                if frame:
                    msg = decode(frame, timestamp)
                    if msg is not None and (msg.is_rx or self._receive_own_messages):
                        self._messages.append(msg)

        # Without flags (e.g. not a supported device), no valid frame can complete the buffer
        if len(self._buffer) > MAX_FRAME_SIZE:
            self._buffer.clear()

    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        """Send a message. Raises ValueError for messages that can't be sent and can.CanOperationError if the message
        could not be written within `timeout` seconds (None: wait indefinitely)."""
        frame = encode(msg)

        with self._write_lock:
            try:
                if self._serial.write_timeout != timeout:
                    self._serial.write_timeout = timeout
                self._serial.write(frame)
            except serial.SerialException as e:
                raise can.CanOperationError(f"Could not write to {self.channel_info}: {e}") from e

    def shutdown(self) -> None:
        super().shutdown()
        self._serial.close()

    @staticmethod
    def _detect_available_configs() -> List[can.typechecking.AutoDetectedConfig]:
        return [
            {"interface": "csscan_serial", "channel": port.device}
            for port in comports()
            if (port.vid, port.pid) in _USB_IDS
        ]
