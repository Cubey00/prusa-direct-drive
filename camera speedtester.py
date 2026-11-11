"""
Speed test for the current acquisition pipeline (threaded TIFF save via queue).

Measures each step of a single snap+save cycle:
  - snap_image (core.snap_image call)
  - numpy reshape
  - queue put (should be ~instant, since save happens in background thread)

Also separately measures the ACTUAL save time inside the worker thread,
so you can see how long TIFF writing takes even though it's off the
critical path of take_photos().

Run this instead of your main script to sanity-check timings without
touching your real acquisition logic.
"""

import time
import threading
import queue
import numpy as np
from pathlib import Path

import tifffile

#from pycromanager import Core
#core = Core()

from ids_camera import IDS_Camera
camera = IDS_Camera()
camera.set_exposure(10e3)

PHOTO_FOLDER = Path(r"C:\Users\Cubefish\Desktop\fototest")
PHOTO_FOLDER.mkdir(parents=True, exist_ok=True)

N_TRIALS = 20  # how many snap+save cycles to time

save_queue = queue.Queue()
save_times = []  # (queue_wait, save_duration) recorded by worker thread


def saver_worker():
    while True:
        item = save_queue.get()
        if item is None:
            break
        arr, filepath, t_enqueued = item

        t_start_save = time.perf_counter()
        tifffile.imwrite(filepath, arr)
        t_end_save = time.perf_counter()

        queue_wait_ms = (t_start_save - t_enqueued) * 1000
        save_ms = (t_end_save - t_start_save) * 1000
        save_times.append((queue_wait_ms, save_ms))


threading.Thread(target=saver_worker, daemon=True).start()


def snap_image_timed():
    t0 = time.perf_counter()

    image = camera.snap()
    t1 = time.perf_counter()

    image_array = np.array(image)
    t2 = time.perf_counter()

    timings = {
        "snap_image_ms": (t1 - t0) * 1000,
        "numpy_ms": (t2 - t1) * 1000,
    }
    return image_array, timings


def main():
    print(f"Running {N_TRIALS} snap+save cycles...\n")

    totals = {"snap_image_ms": [], "numpy_ms": [], "queue_put_ms": [], "loop_total_ms": []}

    for i in range(N_TRIALS):
        t_loop_start = time.perf_counter()

        arr, timings = snap_image_timed()

        t_before_put = time.perf_counter()
        filepath = PHOTO_FOLDER / f"speedtest_{i:03d}.tiff"
        save_queue.put((arr, filepath, time.perf_counter()))
        t_after_put = time.perf_counter()
        timings["queue_put_ms"] = (t_after_put - t_before_put) * 1000

        t_loop_end = time.perf_counter()
        timings["loop_total_ms"] = (t_loop_end - t_loop_start) * 1000

        for k in totals:
            totals[k].append(timings[k])

        print(
            f"[{i:02d}] snap={timings['snap_image_ms']:.1f}ms  "
            f"numpy={timings['numpy_ms']:.1f}ms  "
            f"queue_put={timings['queue_put_ms']:.2f}ms  "
            f"LOOP_TOTAL={timings['loop_total_ms']:.1f}ms"
        )

    # let the background saver drain before reporting save timings
    print("\nWaiting for background saves to finish...")
    while not save_queue.empty():
        time.sleep(0.05)
    time.sleep(0.2)  # small buffer for the last item to be recorded

    print("\n--- Averages over {} trials (main loop, excludes save) ---".format(N_TRIALS))
    for k, v in totals.items():
        print(f"{k:20s}: {sum(v)/len(v):.1f} ms")

    if save_times:
        avg_wait = sum(w for w, _ in save_times) / len(save_times)
        avg_save = sum(s for _, s in save_times) / len(save_times)
        print("\n--- Background saver thread ---")
        print(f"{'avg queue_wait_ms':20s}: {avg_wait:.1f} ms  (time sitting in queue before worker picks it up)")
        print(f"{'avg save_ms':20s}: {avg_save:.1f} ms  (actual tifffile.imwrite duration)")
    else:
        print("\nNo save timing data recorded (queue may not have drained in time).")

    effective_fps = 1000 / (sum(totals["loop_total_ms"]) / len(totals["loop_total_ms"]))
    print(f"\nEffective loop-only FPS (excludes save + any sleep): {effective_fps:.2f} fps")


if __name__ == "__main__":
    main()