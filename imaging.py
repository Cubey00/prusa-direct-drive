import time
from datetime import datetime
import random
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
import threading, queue
import tifffile

from ids_camera import IDS_Camera
camera = IDS_Camera()
camera.set_exposure(50e3)


PHOTO_FOLDER = Path("photos") # folder placeholder
def set_photo_folder(folder):
    """Change the folder where photos are save d."""
    global PHOTO_FOLDER
    PHOTO_FOLDER = Path(folder)
    PHOTO_FOLDER.mkdir(parents=True, exist_ok=True)

set_photo_folder(r"C:\Users\Cubefish\Desktop\printerproject\fototest")


save_queue = queue.Queue()
def saver_worker():
    while True:
        item = save_queue.get()
        if item is None:
            break
        arr, filepath = item
        tifffile.imwrite(filepath, arr)

threading.Thread(target=saver_worker, daemon=True).start()


def image_and_annotate(data):
    # acquire an image using the ids_camera script that talks to IDS peak 
    # and annotate it with the timestamp and the given data and save it 
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    image = np.array(camera.snap())
    
    filename = f"{timestamp}_{data}.tiff"
    filepath = Path(PHOTO_FOLDER) / filename

    save_queue.put((image, filepath))

    print(f"Saved: {filename}")
    return

def single_image():
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