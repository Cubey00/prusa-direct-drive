import threading
import time

from strain_gauge import StrainGauge


def feed(gauge, value, n, delay=0.0005):
    for _ in range(n):
        gauge.samples.append(value)
        gauge._count += 1
        time.sleep(delay)


def test_tare_fresh_ignores_samples_from_before_the_call():
    g = StrainGauge()
    feed(g, 1000.0, 300, delay=0)            # load while the head was moving
    t = threading.Thread(target=feed, args=(g, 10.0, 600))
    t.start()
    assert g.tare_fresh(300, timeout=5)
    t.join()
    assert abs(g.offset - 10.0) < 1e-9


def test_tare_fresh_times_out_without_data():
    g = StrainGauge()
    feed(g, 5.0, 300, delay=0)
    assert not g.tare_fresh(300, timeout=0.1)


def test_firmware_offset_change_does_not_shift_force():
    """The printer re-zeroes its own offset (o=) on homing; the tared force must not jump."""
    g = StrainGauge()
    for _ in range(300):
        g._parse("loadcell r=303000i,o=302600i,s=0.0192 1")
    assert g.tare()
    for _ in range(300):
        g._parse("loadcell r=303000i,o=303000i,s=0.0192 2")
    assert abs(g.force_g()) < 1e-9


def test_position_lines():
    g = StrainGauge()
    g._parse("pos_z v=21.939999 1234")
    assert g.pos["z"] == 21.939999
