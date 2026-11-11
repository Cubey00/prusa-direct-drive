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
