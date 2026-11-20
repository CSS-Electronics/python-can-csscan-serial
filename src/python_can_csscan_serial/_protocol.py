"""Serial protocol of the device USB interface.

Frames are HDLC-like: each frame is enclosed in flag bytes (0x7E), and flag/escape bytes inside a frame are escaped
(0x7D followed by the byte XOR 0x20). A frame ends with a CRC-16 (big endian) over its unescaped content.

Frames from the device: type (1), seconds (4), milliseconds (2), ID (4), DLC (1), data (0-64), CRC (2)
Frames to the device:   type (1), ID (4), DLC (1), data (0-64), CRC (2)

The lower nibble of the type is the kind (0x01: received, 0x02: transmitted, 0x03: transmit request), the upper nibble
holds flags (0x10: remote frame, 0x20: CAN FD). Bit 29 of the ID marks an extended (29-bit) ID.
"""

import struct
from typing import Optional

import can

FLAG = 0x7E
_ESCAPE = 0x7D
_ESCAPE_XOR = 0x20

_KIND_RX = 0x01
_KIND_TX = 0x02
_KIND_TX_REQUEST = 0x03
_FLAG_REMOTE = 0x10
_FLAG_FD = 0x20

_ID_EXTENDED = 0x20000000
_ID_MASK = 0x1FFFFFFF

_RX_HEADER = struct.Struct("!BIHIB")
_TX_HEADER = struct.Struct("!BIB")
_CRC_SIZE = 2

# Largest frame (without flags) from the device: every byte escaped
MAX_FRAME_SIZE = 2 * (_RX_HEADER.size + 64 + _CRC_SIZE)


def crc16(data: bytes) -> int:
    """CRC-16/ARC (polynomial 0xA001 reflected, initial value 0)."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def stuff(content: bytes) -> bytes:
    """Append the CRC, escape flag/escape bytes and enclose in flags."""
    content = bytes(content) + crc16(content).to_bytes(_CRC_SIZE, "big")
    out = bytearray([FLAG])
    for b in content:
        if b in (FLAG, _ESCAPE):
            out += bytes([_ESCAPE, b ^ _ESCAPE_XOR])
        else:
            out.append(b)
    out.append(FLAG)
    return bytes(out)


def unstuff(frame: bytes) -> Optional[bytes]:
    """Revert escaping of a frame (without flags) and verify/remove the CRC. Returns None if the frame is invalid."""
    out = bytearray()
    it = iter(frame)
    for b in it:
        if b == _ESCAPE:
            b = next(it, None)
            if b is None:
                return None
            b ^= _ESCAPE_XOR
        out.append(b)

    if len(out) < _CRC_SIZE or crc16(out[:-_CRC_SIZE]) != int.from_bytes(out[-_CRC_SIZE:], "big"):
        return None
    return bytes(out[:-_CRC_SIZE])


def _validate(msg: can.Message) -> None:
    if msg.is_error_frame:
        raise ValueError("Error frames can't be sent")
    if msg.is_extended_id and not 0 <= msg.arbitration_id <= _ID_MASK:
        raise ValueError(f"Extended ID out of range: 0x{msg.arbitration_id:X}")
    if not msg.is_extended_id and not 0 <= msg.arbitration_id <= 0x7FF:
        raise ValueError(f"Standard ID out of range: 0x{msg.arbitration_id:X}")
    if msg.is_remote_frame:
        if msg.is_fd:
            raise ValueError("CAN FD does not have remote frames")
        if not 0 <= msg.dlc <= 8:
            raise ValueError(f"Remote frame DLC out of range: {msg.dlc}")
    elif len(msg.data) > (64 if msg.is_fd else 8):
        raise ValueError(f"Too much data for a {'CAN FD' if msg.is_fd else 'classic CAN'} frame: {len(msg.data)} bytes")


def encode(msg: can.Message) -> bytes:
    """Encode a message as a transmit request frame (with flags). Raises ValueError for messages that can't be sent."""
    _validate(msg)

    frame_type = _KIND_TX_REQUEST
    if msg.is_remote_frame:
        frame_type |= _FLAG_REMOTE
    if msg.is_fd:
        frame_type |= _FLAG_FD

    can_id = msg.arbitration_id | (_ID_EXTENDED if msg.is_extended_id else 0)

    if msg.is_remote_frame:
        dlc, data = can.util.len2dlc(msg.dlc), b""
    else:
        dlc = can.util.len2dlc(len(msg.data))
        # CAN FD lengths not matching a DLC are padded to the next DLC length
        data = bytes(msg.data).ljust(can.util.dlc2len(dlc), b"\x00")

    return stuff(_TX_HEADER.pack(frame_type, can_id, dlc) + data)


def decode(frame: bytes, timestamp: float) -> Optional[can.Message]:
    """Decode a frame (without flags). Returns None for invalid frames and frames that are not CAN messages.

    The device timestamp is not used, as the device clock is not necessarily synchronized. Messages get `timestamp`.
    """
    content = unstuff(frame)
    if content is None or len(content) < _RX_HEADER.size:
        return None

    frame_type, _seconds, _milliseconds, can_id, dlc = _RX_HEADER.unpack_from(content)
    kind = frame_type & 0x0F
    if kind not in (_KIND_RX, _KIND_TX):
        return None

    length = can.util.dlc2len(dlc)
    is_remote = bool(frame_type & _FLAG_REMOTE)
    data = content[_RX_HEADER.size:_RX_HEADER.size + length]
    if not is_remote and len(data) != length:
        return None

    return can.Message(
        timestamp=timestamp,
        arbitration_id=can_id & _ID_MASK,
        is_extended_id=bool(can_id & _ID_EXTENDED),
        is_remote_frame=is_remote,
        is_fd=bool(frame_type & _FLAG_FD),
        is_rx=kind == _KIND_RX,
        dlc=length,
        data=None if is_remote else data,
    )
