Put the PrusaSlicer profile used for every plate here as coreone.ini.

Make it once in PrusaSlicer 2.9 or newer: select the Core One printer, the print settings and the
filament, then File > Export > Export Config and save it as slicing/profiles/coreone.ini.
python -m slicing always adds --support-material=0 and --dont-arrange on top of it.
