# EnduroSat OBC – RS485 UART Interface

This repository contains Python code to interface with the EnduroSat OBC over RS485 using an FTDI USB-RS485 cable. It pretends to be an receiving unit, and supports all necessary handshakes to receive files from an EnduroSat OBC (via SpaceDev  - `OBC_CP_GWClient` or through a schedule). 

---

##  Hardware

| Device            | Model / Link                                                                     |
| ----------------- | -------------------------------------------------------------------------------- |
| Onboard Computer  | EnduroSat OBC                                                                    |
| USB-RS485 Adapter | [FTDI USB-RS485-WE-1800-BT](https://ftdichip.com/products/usb-rs485-we-1800-bt/) |

---

##  Connection Setup

Connect the FTDI USB-RS485 cable to the OBC's PC-104 header as follows:

| OBC PC-104 Pin     | FTDI Cable Wire |
| ------------------ | --------------- |
| H1-37: RS485\_1\_N | Yellow (Data-)  |
| H1-38: RS485\_1\_P | Orange (Data+)  |
| H2-31: GND         | Black  (Gnd)    |

---

##  Configuration Notes

Before running the script, ensure the following:

### Update COM Port

Set the appropriate serial port in `uart_bid.py`.\
For example:

```python
PORT = '/dev/ttyUSB0'  # or 'COM5' on Windows
```

### Set Gateway ID

The default module ID for the gateway (`GW`) should be defined in the script. Adjust if needed. 

### Latency Timer (for reduced response time)

- **Linux:**\
  Set latency using `udevadm` or by writing directly to the device driver:

  ```bash
  echo 1 | sudo tee /sys/bus/usb-serial/devices/ttyUSB0/latency_timer
  ```

- **Windows:**\
  Open *Device Manager* → your FTDI device → *Port Settings* → *Advanced* → Set **Latency Timer** to **1 ms**.

### Set flags 

The flag `verbose` prints more information to the command line for debugging purposes. If the flag `loguart`is set, these details will be logged to uart_debug.log. If both are not set, the only information that is displayed is succesful file download. 

---

## Running the Interface

To start the UART communication script:

```bash
python uart_bid.py
```

Logs will be saved to `uart_debug.log`.

---

## Receiving Data from the OBC

You can send files from the OBC via **SpaceDev**:

- Use the `OBC_CP_GWClient` utility in SpaceDev.
- Set the identifier to be the same as the module ID set in the python script.
- Setlect UPLOAD_FILE, WRITE and the corresponding file name and length. 
- The script will automatically save received files in the `./Data/` directory under the same name.

---

## Directory Structure

```
.
├── uart_bid.py          # Main script
├── uart_debug.log       # Log file
└── Data/                # Received files saved here
```

---

## Contact

For questions or improvements, feel free to open an issue or pull request.

