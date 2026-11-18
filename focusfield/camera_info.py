"""Read-only camera inventory. Missing controls are recorded as unknown, never assumed off."""
FEATURES = ("PayloadSize", "Width", "Height", "OffsetX", "OffsetY", "BinningHorizontal", "BinningVertical",
            "DecimationHorizontal", "DecimationVertical", "ReverseX", "ReverseY", "PixelColorFilter",
            "BlackLevel", "Gamma", "GammaEnable", "Sharpness", "SharpnessEnable", "ExposureAuto",
            "GainAuto", "ExposureTime", "Gain")


def read_node(nodemap, name):
    try:
        node = nodemap.FindNode(name)
        try:
            value = node.Value()
        except Exception:
            value = node.CurrentEntry().SymbolicValue()
        if type(value) not in (str, int, float, bool):
            value = node.CurrentEntry().SymbolicValue()
        return value if type(value) in (str, int, float, bool) else None
    except Exception:
        return None


def peak_inventory(nodemap, peak):
    entries = []
    try:
        for entry in nodemap.FindNode("PixelFormat").Entries():
            if entry.AccessStatus() not in (peak.NodeAccessStatus_NotAvailable, peak.NodeAccessStatus_NotImplemented):
                entries.append(entry.SymbolicValue())
    except Exception:
        pass
    return dict(backend="ids_peak", available_pixel_formats=entries,
                features={name: read_node(nodemap, name) for name in FEATURES},
                native_pixel_format=read_node(nodemap, "PixelFormat"),
                hardware_validated=False)


def validate_raw_geometry(info, pixel_format):
    f = info["features"]
    if pixel_format.startswith("BayerRG"):
        if any(type(f.get(k)) is not int or f[k] % 2 for k in ("OffsetX", "OffsetY")):
            raise ValueError("BayerRG requires verified even ROI offsets for the RGGB green lattice")
        if any(f.get(k) not in (None, False, 0) for k in ("ReverseX", "ReverseY")):
            raise ValueError("BayerRG reverse axes are unsupported")
    if any(f.get(k) not in (None, 1) for k in ("BinningHorizontal", "BinningVertical",
                                             "DecimationHorizontal", "DecimationVertical")):
        raise ValueError("Optical raw capture requires no binning/subsampling")
