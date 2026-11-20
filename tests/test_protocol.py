"""Tests of the serial protocol (frame encoding and decoding)."""

import can
import pytest

from python_can_csscan_serial._protocol import crc16, decode, encode, stuff, unstuff

# Frames captured from a CL2000 (without flags): extended ID 0x7E / 0x01, data 7E 7D (escaped)
DEVICE_FRAME_1 = bytes.fromhex("01 6b 00 03 a4 01 c1 20 00 00 7d 5e 02 7d 5e 7d 5d 5e 6a")
DEVICE_FRAME_2 = bytes.fromhex("01 6b 00 03 a4 01 c2 20 00 00 01 02 7d 5e 7d 5d 9f 33")


def device_frame(can_id, dlc, data=b"", frame_type=0x01):
    """Frame from the device (without flags). `can_id` includes the extended flag (bit 29)."""
    return stuff(bytes([frame_type]) + bytes(6) + can_id.to_bytes(4, "big") + bytes([dlc]) + bytes(data))[1:-1]


# CRC and stuffing

def test_crc_check_value():
    # CRC-16/ARC check value
    assert crc16(b"123456789") == 0xBB3D


def test_crc_rejects_corruption():
    frame = bytearray(DEVICE_FRAME_2)
    frame[12] ^= 0x01

    assert unstuff(bytes(frame)) is None


def test_stuff_escapes_flag_and_escape():
    assert stuff(b"\x7e\x7d\x00") == bytes.fromhex("7e 7d5e 7d5d 00") + crc16(b"\x7e\x7d\x00").to_bytes(2, "big") + b"\x7e"


def test_unstuff_reverts_stuff():
    content = bytes(range(256))

    assert unstuff(stuff(content)[1:-1]) == content


def test_unstuff_escape_at_end():
    assert unstuff(stuff(b"\x01\x02")[1:-1] + b"\x7d") is None


# Decoding

def test_decode_device_frames():
    for frame, can_id in ((DEVICE_FRAME_1, 0x7E), (DEVICE_FRAME_2, 0x01)):
        msg = decode(frame, timestamp=12.5)

        assert msg.timestamp == 12.5
        assert msg.arbitration_id == can_id
        assert msg.is_extended_id
        assert msg.is_rx
        assert not msg.is_fd
        assert not msg.is_remote_frame
        assert bytes(msg.data) == b"\x7e\x7d"


@pytest.mark.parametrize(
    "frame_type, dlc, data, is_rx, is_fd, is_remote",
    [
        (0x01, 8, range(8), True, False, False),
        (0x02, 8, range(8), False, False, False),
        (0x11, 4, b"", True, False, True),
        (0x12, 4, b"", False, False, True),
        (0x21, 9, range(12), True, True, False),
        (0x22, 9, range(12), False, True, False),
        (0x21, 15, range(64), True, True, False),
    ],
)
def test_decode_frame_types(frame_type, dlc, data, is_rx, is_fd, is_remote):
    msg = decode(device_frame(0x123, dlc, data, frame_type=frame_type), timestamp=0)

    assert (msg.is_rx, msg.is_fd, msg.is_remote_frame) == (is_rx, is_fd, is_remote)
    assert msg.dlc == can.util.dlc2len(dlc)
    assert bytes(msg.data) == bytes(data)


def test_decode_extended_id():
    msg = decode(device_frame(0x3FFFFFFF, 0), timestamp=0)

    assert msg.is_extended_id
    assert msg.arbitration_id == 0x1FFFFFFF


@pytest.mark.parametrize("frame_type", [0x00, 0x03, 0x04, 0x0F])
def test_decode_drops_unknown_frame_types(frame_type):
    assert decode(device_frame(0x123, 2, [1, 2], frame_type=frame_type), timestamp=0) is None


def test_decode_drops_truncated_data():
    assert decode(device_frame(0x123, 8, [1, 2]), timestamp=0) is None


def test_decode_drops_short_frame_with_valid_crc():
    assert decode(stuff(b"\x01\x00")[1:-1], timestamp=0) is None


def test_decode_drops_empty_frame():
    assert decode(b"", timestamp=0) is None


# Encoding

def test_encode_reference_vector():
    # Frame accepted and transmitted by a CL2000
    msg = can.Message(arbitration_id=0x7E, is_extended_id=False, data=[0x7E, 0x7D, 0x00, 0xFF])

    assert encode(msg) == bytes.fromhex("7e 03 00 00 00 7d 5e 04 7d 5e 7d 5d 00 ff a1 c3 7e")


def test_encode_extended_remote():
    msg = can.Message(arbitration_id=0x1ABCDEF0, is_extended_id=True, is_remote_frame=True, dlc=3)

    assert unstuff(encode(msg)[1:-1]) == bytes.fromhex("13 3ABCDEF0 03")


def test_encode_fd_pads_to_dlc_length():
    msg = can.Message(arbitration_id=0x123, is_extended_id=False, is_fd=True, data=range(10))

    assert unstuff(encode(msg)[1:-1]) == bytes.fromhex("23 00000123 09") + bytes(range(10)) + bytes(2)


def test_encode_fd_64():
    msg = can.Message(arbitration_id=0x123, is_extended_id=False, is_fd=True, data=range(64))

    assert unstuff(encode(msg)[1:-1]) == bytes.fromhex("23 00000123 0F") + bytes(range(64))


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(arbitration_id=0x800, is_extended_id=False),
        dict(arbitration_id=0x20000000, is_extended_id=True),
        dict(arbitration_id=-1, is_extended_id=False),
        dict(arbitration_id=0x123, is_extended_id=False, data=range(9)),
        dict(arbitration_id=0x123, is_extended_id=False, is_fd=True, data=range(65)),
        dict(arbitration_id=0x123, is_extended_id=False, is_remote_frame=True, dlc=9),
        dict(arbitration_id=0x123, is_extended_id=False, is_remote_frame=True, is_fd=True),
        dict(arbitration_id=0x123, is_extended_id=False, is_error_frame=True),
    ],
    ids=["std_id", "ext_id", "negative_id", "classic_9", "fd_65", "remote_dlc_9", "remote_fd", "error_frame"],
)
def test_encode_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        encode(can.Message(**kwargs))
