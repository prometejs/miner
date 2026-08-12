"""Per-quantum work accounting.

Every accepted share is credited to the quantum (extranonce1) that produced
it. `implied_hashes` is the statistically expected number of hashes performed
to produce the accepted shares (sum of share difficulties × 2^32) — the
standard hashrate estimator, now attributable per work-slice.
"""

import time
from dataclasses import dataclass, field


@dataclass
class QuantumRecord:
    extranonce1: str
    address: str = ""
    worker: str = ""
    user_agent: str = ""
    session_start: float = field(default_factory=time.time)
    session_end: float | None = None
    accepted_shares: int = 0
    rejected_shares: int = 0
    sum_difficulty: float = 0.0
    best_difficulty: float = 0.0
    last_share_at: float | None = None
    blocks_found: int = 0

    @property
    def implied_hashes(self) -> float:
        return self.sum_difficulty * 4294967296

    @property
    def effective_hashrate(self) -> float:
        end = self.session_end or time.time()
        elapsed = end - self.session_start
        return self.implied_hashes / elapsed if elapsed > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "extranonce1": self.extranonce1,
            "address": self.address,
            "worker": self.worker,
            "userAgent": self.user_agent,
            "sessionStart": self.session_start,
            "sessionEnd": self.session_end,
            "acceptedShares": self.accepted_shares,
            "rejectedShares": self.rejected_shares,
            "sumDifficulty": self.sum_difficulty,
            "bestDifficulty": self.best_difficulty,
            "lastShareAt": self.last_share_at,
            "impliedHashes": self.implied_hashes,
            "effectiveHashrate": self.effective_hashrate,
            "blocksFound": self.blocks_found,
        }


class QuantaLedger:
    def __init__(self) -> None:
        self.quanta: dict[str, QuantumRecord] = {}

    def open(self, extranonce1: str, address: str, worker: str, user_agent: str) -> QuantumRecord:
        record = QuantumRecord(
            extranonce1=extranonce1, address=address, worker=worker, user_agent=user_agent
        )
        self.quanta[extranonce1] = record
        return record

    def close(self, extranonce1: str) -> None:
        record = self.quanta.get(extranonce1)
        if record:
            record.session_end = time.time()

    def record_accepted(self, extranonce1: str, difficulty: float) -> None:
        record = self.quanta.get(extranonce1)
        if not record:
            return
        record.accepted_shares += 1
        record.sum_difficulty += difficulty
        record.last_share_at = time.time()
        if difficulty > record.best_difficulty:
            record.best_difficulty = difficulty

    def record_rejected(self, extranonce1: str) -> None:
        record = self.quanta.get(extranonce1)
        if record:
            record.rejected_shares += 1

    def record_block(self, extranonce1: str) -> None:
        record = self.quanta.get(extranonce1)
        if record:
            record.blocks_found += 1

    def snapshot(self) -> dict:
        active = [q for q in self.quanta.values() if q.session_end is None]
        return {
            "quanta": [q.to_dict() for q in self.quanta.values()],
            "totals": {
                "activeQuanta": len(active),
                "acceptedShares": sum(q.accepted_shares for q in self.quanta.values()),
                "bestDifficulty": max(
                    (q.best_difficulty for q in self.quanta.values()), default=0.0
                ),
                "effectiveHashrate": sum(q.effective_hashrate for q in active),
                "blocksFound": sum(q.blocks_found for q in self.quanta.values()),
            },
        }
