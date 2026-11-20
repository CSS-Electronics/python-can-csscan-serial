"""Live test through a CSS Electronics device with a USB serial interface (e.g. CL2000), using a CANsub interface
(python-can-cansub) as the other node on the bus.

Setup:
    - The device and one CANsub channel connected to the same (classic CAN) bus.
    - The CANsub is opened with the bit rate the device is configured for (default 250 kbit/s).
    - The device does not report its own transmitted messages (CL2000 default).

Remote frames are not tested, as the CL2000 does not support them (received remote frames are reported as data
frames, remote frames to send are ignored).

The tests are skipped unless the channels are given through environment variables, e.g.:

    CSSCAN_SERIAL_CHANNEL=/dev/ttyACM0 \
    CSSCAN_SERIAL_CANSUB=fea7353d-usb.local@1 \
    CSSCAN_SERIAL_BITRATE=250000 \
    pytest -m hardware
"""

import os
import time

import can
import pytest

CHANNEL = os.environ.get("CSSCAN_SERIAL_CHANNEL")
CANSUB = os.environ.get("CSSCAN_SERIAL_CANSUB")
BITRATE = os.environ.get("CSSCAN_SERIAL_BITRATE", "250000")

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(not CHANNEL, reason="CSSCAN_SERIAL_CHANNEL not set"),
]
needs_cansub = pytest.mark.skipif(not CANSUB, reason="CSSCAN_SERIAL_CANSUB not set")

MESSAGES = {
    "std_empty": dict(arbitration_id=0x000, is_extended_id=False),
    "std_8": dict(arbitration_id=0x7FF, is_extended_id=False, data=range(8)),
    # Flag (0x7E) and escape (0x7D) bytes in ID and data
    "std_escaped": dict(arbitration_id=0x07E, is_extended_id=False, data=[0x7E, 0x7D, 0x7E, 0x7D]),
    "ext_3": dict(arbitration_id=0x1ABCDEF0, is_extended_id=True, data=[0xDE, 0xAD, 0xBF]),
    "ext_escaped": dict(arbitration_id=0x1E7D7E7D, is_extended_id=True, data=[0x7D] * 8),
    "ext_max": dict(arbitration_id=0x1FFFFFFF, is_extended_id=True, data=[0xFF] * 8),
}

@pytest.fixture(scope="module")
def device():
    with can.Bus(interface="csscan_serial", channel=CHANNEL) as bus:
        yield bus


@pytest.fixture(scope="module")
def cansub_bus():
    pytest.importorskip("python_can_cansub")
    with can.Bus(interface="cansub", channel=CANSUB, bitrate=int(BITRATE)) as bus:
        yield bus


@pytest.fixture
def cansub(device, cansub_bus):
    """The CANsub bus, with both buses flushed before the test."""
    for bus in (device, cansub_bus):
        receive(bus, idle=0.05)
    return cansub_bus


def receive(bus, idle=0.5):
    """Receive messages until the bus has been idle for `idle` seconds."""
    return list(iter(lambda: bus.recv(timeout=idle), None))


def assert_transferred(received, expected):
    assert received.arbitration_id == expected.arbitration_id
    assert received.is_extended_id == expected.is_extended_id
    assert received.is_remote_frame == expected.is_remote_frame
    assert received.dlc == expected.dlc
    assert bytes(received.data) == bytes(expected.data)


def test_detect_available_configs():
    assert {"interface": "csscan_serial", "channel": CHANNEL} in can.detect_available_configs("csscan_serial")


def test_recv_timeout(device):
    receive(device, idle=0.05)
    start = time.monotonic()

    assert device.recv(timeout=0.2) is None
    assert 0.2 <= time.monotonic() - start < 1.2


@needs_cansub
@pytest.mark.parametrize("name", MESSAGES)
def test_receive(device, cansub, name):
    expected = can.Message(**MESSAGES[name])

    before = time.time()
    cansub.send(expected)
    received = receive(device)

    assert len(received) == 1
    assert_transferred(received[0], expected)
    assert received[0].is_rx
    assert before <= received[0].timestamp <= time.time()


@needs_cansub
@pytest.mark.parametrize("name", MESSAGES)
def test_send(device, cansub, name):
    expected = can.Message(**MESSAGES[name])

    device.send(expected, timeout=1)
    received = receive(cansub)

    assert len(received) == 1
    assert_transferred(received[0], expected)
    assert receive(device, idle=0.1) == []


@needs_cansub
def test_receive_burst(device, cansub):
    expected = [can.Message(arbitration_id=i, is_extended_id=False, data=i.to_bytes(2, "big")) for i in range(200)]

    for msg in expected:
        cansub.send(msg)
    received = receive(device)

    assert len(received) == len(expected)
    for r, e in zip(received, expected):
        assert_transferred(r, e)


@needs_cansub
def test_send_burst(device, cansub):
    expected = [can.Message(arbitration_id=0x100 + i, is_extended_id=True, data=[i] * 8) for i in range(100)]

    for msg in expected:
        device.send(msg, timeout=1)
        # Pace the messages, so the test does not depend on the size of the device transmit queue
        time.sleep(0.002)
    received = receive(cansub)

    assert len(received) == len(expected)
    for r, e in zip(received, expected):
        assert_transferred(r, e)
