"""Tests never open a real camera: the Foto control panel's live feed stays off."""
import os

os.environ.setdefault("PRINTERPROJECT_NO_CAMERA", "1")
