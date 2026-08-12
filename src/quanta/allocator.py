"""Quanta allocation: which slice of the search space each miner connection
explores.

A miner's extranonce1 IS its quantum: it is embedded in the coinbase, so every
(extranonce1, extranonce2, version-bits, ntime, nonce) tuple it hashes is
provably disjoint from every other miner's. The allocator decides how the
extranonce1 space is carved up:

- "random": 4 random bytes per connection (collision-checked and retried)
- "sequential": deterministic incrementing counter — quanta are assigned in a
  defined work sequence and the ledger can show exactly which slices have been
  explored in which order

NOTE (documented honesty): allocation policy has zero effect on expected
time-to-block — SHA-256d is uniform and memoryless. The policy exists for
work-space bookkeeping, telemetry, and duplicate-work prevention only.
"""

import secrets

from ..bitcoin.coinbase import EXTRANONCE1_SIZE_BYTES


class QuantaAllocator:
    def __init__(self, policy: str = "random"):
        if policy not in ("random", "sequential"):
            raise ValueError(f"unknown quanta policy: {policy}")
        self.policy = policy
        self._counter = 0
        self._active: set[str] = set()

    def allocate(self) -> str:
        if self.policy == "sequential":
            while True:
                self._counter += 1
                extranonce1 = format(self._counter, f"0{EXTRANONCE1_SIZE_BYTES * 2}x")
                if extranonce1 not in self._active:
                    break
        else:
            while True:
                extranonce1 = secrets.token_hex(EXTRANONCE1_SIZE_BYTES)
                if extranonce1 not in self._active:
                    break
        self._active.add(extranonce1)
        return extranonce1

    def release(self, extranonce1: str) -> None:
        self._active.discard(extranonce1)
