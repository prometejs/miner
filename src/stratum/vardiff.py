"""Per-session share statistics and vardiff.

State is in-memory (durable accounting lives in the quanta ledger). The
retargeter aims for a steady share cadence and snaps difficulty to powers of
two so miners see stable, predictable adjustments.
"""

import math
import time

CACHE_SIZE = 30
TARGET_SUBMISSION_PER_SECOND = 10  # yields ~1 share every 10 seconds per session
MIN_DIFF = 0.00001


class SessionStatistics:
    def __init__(self) -> None:
        self.shares: float = 0
        self.accepted_count: int = 0
        self.hash_rate: float = 0
        self._cache_start = time.time()
        self._cache: list[tuple[float, float]] = []  # (time, difficulty)
        self._window_start = time.time()
        self._previous_shares: float = 0

    def add_share(self, target_difficulty: float) -> None:
        now = time.time()
        if len(self._cache) > CACHE_SIZE:
            self._cache.pop(0)
        self._cache.append((now, target_difficulty))

        self.shares += target_difficulty
        self.accepted_count += 1
        if self.shares > 0:
            elapsed = now - self._window_start
            if elapsed > 0:
                self.hash_rate = ((self._previous_shares + self.shares) * 4294967296) / elapsed

    def suggested_difficulty(self, client_difficulty: float) -> float | None:
        """Returns a new difficulty or None to keep the current one."""
        if len(self._cache) < 5:
            # miner hasn't submitted 5 shares yet; after a minute, drop difficulty
            if time.time() - self._cache_start > 60:
                return nearest_power_of_two(client_difficulty / 6)
            return None

        total = sum(difficulty for _, difficulty in self._cache)
        elapsed = self._cache[-1][0] - self._cache[0][0]
        if elapsed <= 0:
            return None
        difficulty_per_second = total / elapsed
        target_difficulty = difficulty_per_second * TARGET_SUBMISSION_PER_SECOND

        if (client_difficulty * 2) < target_difficulty or (client_difficulty / 2) > target_difficulty:
            return nearest_power_of_two(target_difficulty)
        return None


def nearest_power_of_two(val: float) -> float | None:
    if val == 0:
        return None
    if val < MIN_DIFF:
        return MIN_DIFF
    if val >= 1:
        return float(2 ** math.floor(math.log2(val)))
    # fractional difficulties: scale into integer range, snap, scale back
    if val * 100 < MIN_DIFF:
        return MIN_DIFF
    scaled = nearest_power_of_two(val * 100)
    return scaled / 100 if scaled is not None else None
