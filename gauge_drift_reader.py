#gauge_drift_reader
import time, json
from strain_gauge import StrainGauge, enable_metrics

import numpy as np
import matplotlib.pyplot as plt

from pathlib import Path

script_folder = Path(__file__).resolve().parent

def analyze_drift(filename="drift_log.json"):
    with open(script_folder / filename) as f:
        samples = json.load(f)
    t, v = zip(*samples)
    t, v = np.array(t), np.array(v)

    # linear fit
    a1, b1 = np.polyfit(t, v, 1)
    pred_lin = a1 * t + b1
    rmse_lin = np.sqrt(np.mean((v - pred_lin) ** 2))

    # quadratic fit, in case it's not linear
    a2, b2, c2 = np.polyfit(t, v, 2)
    pred_quad = a2 * t**2 + b2 * t + c2
    rmse_quad = np.sqrt(np.mean((v - pred_quad) ** 2))

    print(f"Linear fit:    formula={a1:.5f} * t + {b1:.5f}  RMSE={rmse_lin:.3f}")
    print(f"Quadratic fit: formula={a2:.5f} * t^2 + {b2:.5f} * t + {c2:.5f} RMSE={rmse_quad:.3f}")

    drift = StrainGauge.grams(a1)
    drift_pmin = drift * 60
    print(f"Drift is linearly {drift:.5f}g/s or {drift_pmin:.5f}g/min")

    plt.figure(figsize=(8, 4))
    plt.plot(t, v, alpha=0.4, label="raw")
    plt.plot(t, pred_lin, label=f"linear fit (formula={a1:.5f} * t + {b1:.5f}")
    plt.plot(t, pred_quad, label=f"quadratic fit formula={a2:.5f} * t^2 + {b2:.5f} * t + {c2:.5f}")
    plt.xlabel("Time (s)")
    plt.ylabel("Load cell reading")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.show()
    return a1, b1

gauge = StrainGauge(debug=False)

print("Enabling metrics...")
try:
    enable_metrics()
except Exception as e:
    print(f"Metrics Error: {e}")

gauge.start()          # starts the UDP listener thread
print("Metrics enabled, waiting for data...")

while not gauge.can_tare():
    gauge.drain()
    time.sleep(0.1)
    print(f"Waiting... {len(gauge.samples)}/{gauge.TARE_SAMPLES} samples")

print("Taring...")
gauge.tare()
print("Tared!")

def log_drift(gauge, duration_s=10, interval_s=0.1, filename="drift_log.json"):
    samples = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < duration_s:
        gauge.drain()              
        s = gauge.recent(10)
        if s:
            samples.append((time.monotonic() - t0, sum(s) / len(s) - gauge.offset))
        time.sleep(interval_s)
    with open(script_folder / filename, "w") as f:
        json.dump(samples, f)
    print(f"Logged {len(samples)} points over {duration_s}s to {script_folder / filename}")
    return samples

log_drift(gauge, duration_s=1200)
gauge.stop()
analyze_drift()