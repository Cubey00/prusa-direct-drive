import time
from datetime import datetime
import random
import numpy as np
from pathlib import Path
import threading, queue
camera = None
_camera_lock = threading.Lock()


def get_camera():
    """Open the legacy IDS camera only when capture is explicitly requested."""
    global camera
    with _camera_lock:
        if camera is None:
            try:
                from ids_camera import IDS_Camera
            except ImportError as exc:
                raise RuntimeError(
                    "Legacy capture requires the IDS peak SDK. Printer-only use does not; "
                    "see docs/macos-setup.md for the optional simulator workflow."
                ) from exc
            candidate = IDS_Camera()
            try:
                candidate.set_exposure(50e3)
            except Exception:
                candidate.close()
                raise
            camera = candidate
        return camera


PHOTO_FOLDER = Path("photos") # folder placeholder
def set_photo_folder(folder):
    """Change the folder where photos are save d."""
    global PHOTO_FOLDER
    PHOTO_FOLDER = Path(folder)
    PHOTO_FOLDER.mkdir(parents=True, exist_ok=True)

save_queue = queue.Queue()
_saver_thread = None
_saver_lock = threading.Lock()


def saver_worker():
    import tifffile
    while True:
        item = save_queue.get()
        if item is None:
            break
        arr, filepath = item
        tifffile.imwrite(filepath, arr)

def start_saver():
    """No filesystem or background-worker side effects when importing this module."""
    global _saver_thread
    with _saver_lock:
        if _saver_thread is None:
            # Fail on the caller before queuing data if the optional writer is absent.
            try:
                import tifffile
            except ImportError as exc:
                raise RuntimeError("Legacy TIFF saving requires pip install tifffile") from exc
            PHOTO_FOLDER.mkdir(parents=True, exist_ok=True)
            _saver_thread = threading.Thread(target=saver_worker, daemon=True)
            _saver_thread.start()


def image_and_annotate(data):
    # acquire an image using the ids_camera script that talks to IDS peak 
    # and annotate it with the timestamp and the given data and save it 
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    start_saver()
    image = np.array(get_camera().snap())
    
    filename = f"{timestamp}_{data}.tiff"
    filepath = Path(PHOTO_FOLDER) / filename

    save_queue.put((image, filepath))

    print(f"Saved: {filename}")
    return

def single_image():
    camera = get_camera()
    camera.flush()
    image = np.array(camera.snap())
    return image


#ALL BELOW IS JUST FOR TESTING, THE ACTUAL FUNCTION MUST BE WRITTEN WHEN API IS KNOWN
"""
threshold = 50
delay = 0.01
def take_photos():
    while True:
        # testing data
        data = random.randint(0, 100)
        #data = str(data).replace(" ", "_") # to make absolutely sure the filename is valid
        print(f"Data: {data}")

        if data >= threshold:
            print("Threshold reached! Take picture.")
            image_and_annotate(data)
            time.sleep(delay)
        else:
            time.sleep(delay)
    return

while True:
    take_photos()
#"""
