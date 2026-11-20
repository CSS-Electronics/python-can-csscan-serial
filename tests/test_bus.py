"""Tests of the bus, using a pyserial loopback port (loop://) in place of a device."""

import threading
import time

import can
import pytest
import serial

from python_can_csscan_serial import CSSCANSerialBus, _bus
from python_can_csscan_serial._protocol import MAX_FRAME_SIZE, stuff, unstuff

# Frames captured from a CL2000 (with flags): extended ID 0x7E / 0x01, data 7E 7D (escaped)
DEVICE_FRAME_1 = bytes.fromhex("7e 01 6b 00 03 a4 01 c1 20 00 00 7d 5e 02 7d 5e 7d 5d 5e 6a 7e")
DEVICE_FRAME_2 = bytes.fromhex("7e 01 6b 00 03 a4 01 c2 20 00 00 01 02 7d 5e 7d 5d 9f 33 7e")

# Generous upper bound for timing assertions, to avoid flaky tests on loaded machines
SLACK = 1.0


def device_frame(arbitration_id, data, frame_type=0x01):
    return stuff(bytes([frame_type]) + bytes(6) + arbitration_id.to_bytes(4, "big") + bytes([len(data)]) + bytes(data))


@pytest.fixture
def bus():
    with can.Bus(interface="csscan_serial", channel="loop://") as bus:
        yield bus


def feed(bus, data):
    # The loop:// buffer holds 4096 bytes, and writing more blocks forever
    assert len(data) <= 4096
    bus._serial.write(data)


# Opening

def test_open_fails():
    with pytest.raises(can.CanInitializationError):
        can.Bus(interface="csscan_serial", channel="/dev/does-not-exist")


def test_open_invalid_url():
    with pytest.raises(can.CanInitializationError):
        can.Bus(interface="csscan_serial", channel="bogus://x")


def test_channel_required():
    with pytest.raises(can.CanInitializationError):
        CSSCANSerialBus(channel=None)


def test_open_closes_port_on_reset_failure(monkeypatch):
    port = serial.serial_for_url("loop://")

    def fail():
        raise serial.SerialException("Reset failed")

    monkeypatch.setattr(port, "reset_input_buffer", fail)
    monkeypatch.setattr(_bus.serial, "serial_for_url", lambda *args, **kwargs: port)

    with pytest.raises(can.CanInitializationError):
        can.Bus(interface="csscan_serial", channel="loop://")
    assert not port.is_open


def test_shutdown_closes_port():
    with can.Bus(interface="csscan_serial", channel="loop://") as bus:
        port = bus._serial

    assert not port.is_open


# Receiving

def test_recv(bus):
    feed(bus, DEVICE_FRAME_1 + DEVICE_FRAME_2)

    assert bus.recv(1).arbitration_id == 0x7E
    assert bus.recv(1).arbitration_id == 0x01
    assert bus.recv(0) is None


def test_recv_zero_timeout_returns_pending(bus):
    feed(bus, DEVICE_FRAME_1)

    assert bus.recv(0).arbitration_id == 0x7E


def test_recv_timeout(bus):
    start = time.monotonic()

    assert bus.recv(0.1) is None
    assert 0.1 <= time.monotonic() - start < 0.1 + SLACK


def test_recv_timeout_with_undecodable_data(bus, monkeypatch):
    # Data keeps arriving, but never completes a valid frame
    monkeypatch.setattr(type(bus._serial), "in_waiting", property(lambda _: 16))
    monkeypatch.setattr(bus._serial, "read", lambda size: b"\x00" * size)

    start = time.monotonic()

    assert bus.recv(0.1) is None
    assert time.monotonic() - start < 0.1 + SLACK


def test_recv_timestamp(bus):
    feed(bus, DEVICE_FRAME_1)

    before = time.time()
    msg = bus.recv(1)

    assert before <= msg.timestamp <= time.time()


@pytest.mark.parametrize("split", range(1, len(DEVICE_FRAME_1)))
def test_recv_frame_split_across_reads(bus, split):
    feed(bus, DEVICE_FRAME_1[:split])
    assert bus.recv(0.02) is None

    feed(bus, DEVICE_FRAME_1[split:])
    assert bus.recv(1).arbitration_id == 0x7E


def test_recv_resynchronizes_mid_stream(bus):
    # Start in the middle of a frame (as when connecting while the device is sending)
    feed(bus, DEVICE_FRAME_1[5:] + DEVICE_FRAME_2 + DEVICE_FRAME_1)

    assert bus.recv(1).arbitration_id == 0x01
    assert bus.recv(1).arbitration_id == 0x7E
    assert bus.recv(0) is None


def test_recv_skips_garbage(bus):
    feed(bus, b"\x00\x7e\x7e\x7d\x7e\x01\x02" + DEVICE_FRAME_1)

    assert bus.recv(1).arbitration_id == 0x7E
    assert bus.recv(0) is None


def test_recv_bounds_buffer_without_flags(bus):
    feed(bus, b"\x00" * 4000)

    assert bus.recv(0.05) is None
    assert len(bus._buffer) <= MAX_FRAME_SIZE

    # Receives once frames arrive
    feed(bus, DEVICE_FRAME_1 + DEVICE_FRAME_2)
    assert bus.recv(1).arbitration_id == 0x7E
    assert bus.recv(1).arbitration_id == 0x01


def test_recv_many(bus):
    frames = [device_frame(i, i.to_bytes(2, "big")) for i in range(0x7E, 0x7E + 500)]

    ids = []
    for i in range(0, len(frames), 100):
        feed(bus, b"".join(frames[i:i + 100]))
        ids += [bus.recv(1).arbitration_id for _ in range(100)]

    assert ids == list(range(0x7E, 0x7E + 500))


def test_recv_drops_own_messages(bus):
    feed(bus, device_frame(0x100, [1], frame_type=0x02) + device_frame(0x200, [2]))

    assert bus.recv(1).arbitration_id == 0x200
    assert bus.recv(0) is None


def test_recv_own_messages():
    with can.Bus(interface="csscan_serial", channel="loop://", receive_own_messages=True) as bus:
        feed(bus, device_frame(0x100, [1], frame_type=0x02))

        msg = bus.recv(1)

    assert msg.arbitration_id == 0x100
    assert not msg.is_rx


def test_recv_serial_error(bus, monkeypatch):
    def fail(*_):
        raise serial.SerialException("Device disconnected")

    monkeypatch.setattr(bus._serial, "read", fail)

    with pytest.raises(can.CanOperationError):
        bus.recv(0.1)


def test_filters(bus):
    bus.set_filters([{"can_id": 0x01, "can_mask": 0x1FFFFFFF, "extended": True}])
    feed(bus, DEVICE_FRAME_1 + DEVICE_FRAME_2)

    assert bus.recv(1).arbitration_id == 0x01
    assert bus.recv(0) is None


# Sending

def test_send(bus):
    bus.send(can.Message(arbitration_id=0x7E, is_extended_id=False, data=[0x7E, 0x7D, 0x00, 0xFF]))

    assert bus._serial.read(100) == bytes.fromhex("7e 03 00 00 00 7d 5e 04 7d 5e 7d 5d 00 ff a1 c3 7e")


def test_send_invalid(bus):
    with pytest.raises(ValueError):
        bus.send(can.Message(arbitration_id=0x800, is_extended_id=False))


def test_send_serial_error(bus, monkeypatch):
    def fail(*_):
        raise serial.SerialTimeoutException("Write timeout")

    monkeypatch.setattr(bus._serial, "write", fail)

    with pytest.raises(can.CanOperationError):
        bus.send(can.Message(arbitration_id=0x123, is_extended_id=False), timeout=0.1)


def test_send_timeout_sets_write_timeout(bus):
    bus.send(can.Message(arbitration_id=0x123, is_extended_id=False), timeout=0.5)

    assert bus._serial.write_timeout == 0.5


def test_send_from_threads(bus):
    """Frames sent from several threads are not interleaved."""
    def send(base):
        for i in range(25):
            bus.send(can.Message(arbitration_id=base + i, is_extended_id=False, data=[0x7E] * 8))

    threads = [threading.Thread(target=send, args=(base,)) for base in (0x100, 0x200, 0x300, 0x400)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    frames = [f for f in bus._serial.read(4096).split(b"\x7e") if f]

    assert len(frames) == 100
    assert all(unstuff(f) is not None for f in frames)


# Detection

def test_detect_available_configs(monkeypatch):
    class Port:
        def __init__(self, device, vid, pid):
            self.device, self.vid, self.pid = device, vid, pid

    ports = [
        Port("COM1", 0x1CBE, 0x021A),  # CLX000
        Port("COM2", 0x04D8, 0xEB18),  # CANmod
        Port("COM3", 0x04D8, 0x021A),  # Mixed VID/PID
        Port("COM4", None, None),
    ]
    monkeypatch.setattr(_bus, "comports", lambda: ports)

    assert CSSCANSerialBus._detect_available_configs() == [
        {"interface": "csscan_serial", "channel": "COM1"},
        {"interface": "csscan_serial", "channel": "COM2"},
    ]
