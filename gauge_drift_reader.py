"""Log and analyse stationary load-cell drift; importing this module opens no devices."""
import argparse
import json
import math
import sys
import time

import gantry_controls as gc
from strain_gauge import StrainGauge

import numpy as np

from pathlib import Path

script_folder = Path(__file__).resolve().parent

def analyze_drift(filename="drift_log.json"):
    import matplotlib.pyplot as plt

    with open(script_folder / filename, encoding="utf-8") as f:
        samples = json.load(f)
    if len(samples) < 3:
        raise ValueError("Drift analysis requires at least three logged samples")
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

def log_drift(gauge, duration_s=10, interval_s=0.1, filename="drift_log.json"):
    samples = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < duration_s:
        gauge.drain()              
        s = gauge.recent(10)
        if s:
            samples.append((time.monotonic() - t0, sum(s) / len(s) - gauge.offset))
        time.sleep(interval_s)
    path = script_folder / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(samples, f)
    print(f"Logged {len(samples)} points over {duration_s}s to {script_folder / filename}")
    return samples

def _positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of seconds")
    return seconds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=_positive_seconds, default=1200,
                        help="measurement duration in seconds (default: 1200)")
    parser.add_argument("--interval", type=_positive_seconds, default=0.1,
                        help="sampling interval in seconds (default: 0.1)")
    parser.add_argument("--startup-timeout", type=_positive_seconds, default=30,
                        help="maximum wait for initial load-cell data (default: 30 seconds)")
    parser.add_argument("--output", type=Path, default=Path("drift_log.json"),
                        help="JSON output; relative paths are resolved beside this script")
    args = parser.parse_args(argv)
    gauge = None
    try:
        try:
            print("Enabling metrics...")
            gc.enable_metrics()
            gauge = StrainGauge(debug=False)
            gauge.start()
            print("Metrics enabled, waiting for data...")
            deadline = time.monotonic() + args.startup_timeout
            while not gauge.can_tare():
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Timed out waiting for load-cell UDP data on port 8514; "
                        "check printer metrics, PRINTER_METRICS_IP, routing and firewall settings"
                    )
                gauge.drain()
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            if not gauge.tare():
                raise RuntimeError("Could not tare the load cell; no usable initial data")
            print("Tared!")
            log_drift(gauge, duration_s=args.duration, interval_s=args.interval, filename=args.output)
        finally:
            try:
                if gauge is not None:
                    gauge.stop()
                    # stop() signals the listener; wait for its socket timeout/close too.
                    if gauge._thread is not None:
                        gauge._thread.join(timeout=1)
                        if gauge._thread.is_alive():
                            raise RuntimeError("Load-cell UDP listener did not stop within one second")
            finally:
                gc.disconnect()
        analyze_drift(args.output)
    except Exception as exc:
        print(f"Drift experiment failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
