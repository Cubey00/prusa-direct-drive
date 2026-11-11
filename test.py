from gantry_controls import send_gcode
from strain_gauge import enable_metrics


print("Testing serial connection...")
try:
    resp = send_gcode("M115")
    print("Serial OK:", resp[:100])
except Exception as e:
    print(f"SERIAL FAILED: {type(e).__name__}: {e}")
    raise SystemExit("Fix the serial connection before continuing.")

print("Enabling metrics...")
try:
    enable_metrics()
    print("Metrics commands sent successfully.")
except Exception as e:
    print(f"METRICS FAILED: {type(e).__name__}: {e}")
    raise SystemExit("Fix metrics setup before continuing.")