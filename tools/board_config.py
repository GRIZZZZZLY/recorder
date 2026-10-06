"""Write key=value settings to the recorder board over USB serial.

Reads lines like "wifi_ssid2=Office" from stdin, sends them to the board and
prints only the board's Wi-Fi/server summary (never passwords or the token).

    printf 'wifi_ssid2=%s\n' "$S" | python tools/board_config.py [COM5]
"""
import sys
import time

import serial  # pip install pyserial

port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
lines = [l.rstrip("\r\n") for l in sys.stdin if l.strip()]
p = serial.Serial(port, 115200, timeout=0.2)
time.sleep(4)  # opening the port may reset the board
p.read(100000)
for line in lines:
    p.write((line + "\n").encode())
    time.sleep(0.8)
p.read(100000)
p.write(b"c\n")
time.sleep(2)
out = p.read(100000).decode("utf-8", "replace")
keep = ("wifi_ssid", "server_url", "device_token")
print("\n".join(x for x in out.splitlines() if x.startswith(keep)))
