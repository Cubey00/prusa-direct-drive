import serial
import time

port_name = 'COM5'# Replace COM5 with relevant port
ser = serial.Serial(port_name, 115200, timeout=1) 
# Note: The port selection REALLY needs to be either automated or have manual input!!!!!!
# Otherwise VERY BAD

time.sleep(2) # Wait time for serial boot handshake

def send_gcode(cmd):
    ser.write(f"{cmd}\n".encode())
    response = ""
    while True:
        line = ser.readline().decode('utf-8', errors='ignore')
        response += line
        if "ok" in line:
            break
    return response

print(send_gcode("M333"))
#print(send_gcode("G1 Y-100 X-100 F100"))


