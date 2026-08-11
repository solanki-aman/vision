"""The delta gate — the piece that decides whether a refresh costs a model call.

Pure unit tests, no database. The gate is the whole cost story (ProAct: undirected
idle compute burned 69.8k tokens for 0.9%), so it is the thing most worth pinning down
independently of everything it plugs into.
"""

from app.ambient import Delta, _series_volatility, _watch_crossed


def test_a_move_within_volatility_does_not_clear():
    # a figure that normally swings by ~5 moving by 4 is noise, not news
    d = Delta(label="x", old=100.0, new=104.0, volatility=5.0)
    assert not d.clears(1.5)


def test_a_move_beyond_volatility_clears():
    d = Delta(label="x", old=100.0, new=112.0, volatility=5.0)
    assert d.clears(1.5)


def test_a_noisy_figure_needs_a_bigger_move():
    # same absolute move, different noise floors — the noisy one stays quiet
    quiet = Delta(label="x", old=100.0, new=108.0, volatility=2.0)
    noisy = Delta(label="x", old=100.0, new=108.0, volatility=8.0)
    assert quiet.clears(1.5)
    assert not noisy.clears(1.5)


def test_one_percent_absolute_floor_catches_a_flat_series():
    # a series with zero historical volatility still needs a real move, not any move
    d = Delta(label="x", old=100.0, new=100.5, volatility=0.0)
    assert not d.clears(1.5)  # 0.5% < 1% floor
    d2 = Delta(label="x", old=100.0, new=102.0, volatility=0.0)
    assert d2.clears(1.5)  # 2% > 1% floor


def test_missing_endpoints_never_clear():
    assert not Delta(label="x", old=None, new=100.0, volatility=1.0).clears(1.5)
    assert not Delta(label="x", old=100.0, new=None, volatility=1.0).clears(1.5)


def test_volatility_of_a_series():
    # steady climb by 10 each step → zero volatility in the *changes*
    steady = [{"x": str(i), "y": float(i * 10)} for i in range(6)]
    assert _series_volatility(steady) == 0.0
    # a jump in the last step raises it
    jumpy = [{"x": "a", "y": 10.0}, {"x": "b", "y": 20.0}, {"x": "c", "y": 90.0}]
    assert _series_volatility(jumpy) > 0.0


def test_watch_below_fires_only_on_the_crossing():
    watch = {"path": "value", "op": "below", "value": 36.0}
    # was above, now below → crossed
    assert _watch_crossed(watch, Delta("m", old=38.0, new=35.0, volatility=1.0))
    # already below last time → not a fresh crossing
    assert not _watch_crossed(watch, Delta("m", old=35.0, new=34.0, volatility=1.0))
    # stayed above → no
    assert not _watch_crossed(watch, Delta("m", old=40.0, new=37.0, volatility=1.0))


def test_watch_above_fires_only_on_the_crossing():
    watch = {"path": "value", "op": "above", "value": 100.0}
    assert _watch_crossed(watch, Delta("m", old=95.0, new=105.0, volatility=1.0))
    assert not _watch_crossed(watch, Delta("m", old=101.0, new=110.0, volatility=1.0))
