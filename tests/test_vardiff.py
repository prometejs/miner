import time

from prometejs_miner.stratum.vardiff import (
    MIN_DIFF,
    SessionStatistics,
    nearest_power_of_two,
)


def test_nearest_power_of_two():
    assert nearest_power_of_two(0) is None
    assert nearest_power_of_two(1) == 1
    assert nearest_power_of_two(2) == 2
    assert nearest_power_of_two(3) == 2
    assert nearest_power_of_two(1000) == 512
    assert nearest_power_of_two(65536) == 65536
    assert nearest_power_of_two(1e-9) == MIN_DIFF
    # fractional path (*100 scaling): 0.1 → npot(10)/100 = 8/100
    assert nearest_power_of_two(0.1) == 0.08


def test_no_retarget_within_band():
    stats = SessionStatistics()
    now = time.time()
    # 6 shares at difficulty 100 over 6 seconds → ~100 diff/s → target 1000... out of band
    stats._cache = [(now - 6 + i, 100.0) for i in range(7)]
    suggested = stats.suggested_difficulty(1024)
    # target = (700/6)*10 ≈ 1166; 1024*2 > 1166 and 1024/2 < 1166 → keep
    assert suggested is None


def test_retarget_up_when_miner_is_fast():
    stats = SessionStatistics()
    now = time.time()
    # 30 shares of difficulty 1000 over 3 seconds → 10000 diff/s → target 100000
    stats._cache = [(now - 3 + i * 0.1, 1000.0) for i in range(31)]
    suggested = stats.suggested_difficulty(1000)
    assert suggested == 65536  # nearest power of two ≤ 100000


def test_retarget_down_when_miner_is_slow():
    stats = SessionStatistics()
    stats._cache_start = time.time() - 61  # no shares for over a minute
    suggested = stats.suggested_difficulty(65536)
    assert suggested == nearest_power_of_two(65536 / 6) == 8192


def test_hashrate_estimation():
    stats = SessionStatistics()
    stats._window_start = time.time() - 10
    stats.add_share(1000.0)
    # 1000 difficulty over ~10s → ~1000*2^32/10 ≈ 4.29e11 H/s
    assert stats.hash_rate > 0
    assert abs(stats.hash_rate - (1000 * 4294967296 / 10)) / stats.hash_rate < 0.05
