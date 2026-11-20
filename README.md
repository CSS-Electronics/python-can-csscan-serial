# python-can-csscan-serial

Adds the USB interface of [CSS Electronics](https://www.csselectronics.com/) CLX000 and CANmod devices to [python-can](https://python-can.readthedocs.io/), as the interface `csscan_serial`.

```
pip install python-can-csscan-serial
```

## Finding the serial port

The device is listed as a USB virtual serial port. Only one process can open a serial port at a time.

| Device | USB VID:PID |
|---|---|
| CLX000 series (e.g. CL2000) | `1CBE:021A` |
| CANmod series | `04D8:EB18` |

- Windows: `Device Manager` > `Ports (COM & LPT)`, e.g. `COM19`.
- Linux: e.g. `/dev/ttyACM0`. Run `journalctl -f` while connecting the device to see the assigned port.

python-can can also find the connected devices:

```python
>>> can.detect_available_configs("csscan_serial")
[{'interface': 'csscan_serial', 'channel': '/dev/ttyACM0'}]
```

## Usage

```python
import can

with can.Bus(interface="csscan_serial", channel="/dev/ttyACM0") as bus:
    # Send a message
    bus.send(can.Message(arbitration_id=0x123, is_extended_id=False, data=[0x01, 0x23, 0x45, 0x67]))

    # Receive messages
    for msg in bus:
        print(msg)
```

The bit rate and other bus settings are set in the device configuration. python-can arguments such as `bitrate` are ignored.


The interface also works with the python-can [command-line tools](https://python-can.readthedocs.io/en/stable/scripts.html), e.g.:

```
can_logger --interface csscan_serial --channel /dev/ttyACM0
can_viewer --interface csscan_serial --channel /dev/ttyACM0
can_player --interface csscan_serial --channel /dev/ttyACM0 dump.log
```
