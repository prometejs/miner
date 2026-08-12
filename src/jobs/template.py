"""Job templates built from getblocktemplate.

We trust the `txid`/`hash` fields the node provides in the template rather
than re-deriving them from raw bytes, and keep the raw `data` hex for block
assembly. Weight bookkeeping (header + placeholder coinbase + sum of template
weights) only influences the pool-identifier drop within ~600 WU of the 4M cap.
"""

import time
from dataclasses import dataclass

from ..bitcoin.crypto import fold_merkle_branch, hash256, merkle_branch, merkle_root
from ..bitcoin.difficulty import network_difficulty_from_bits
from ..bitcoin.encoding import varint

# weight of an empty placeholder coinbase, counted so the identifier-drop
# check sees the whole block (51 non-witness bytes ×3 + 87 witness bytes)
_TEMP_COINBASE_WEIGHT = 51 * 3 + 87


@dataclass
class JobTemplate:
    id: str
    creation: float  # epoch seconds
    height: int
    coinbasevalue: int
    network_difficulty: float
    clear_jobs: bool
    version: int
    bits: int
    prev_hash_le: bytes  # internal byte order (reversed template hex)
    timestamp: int
    merkle_branches: list[bytes]
    witness_commitment: bytes
    raw_transactions: list[str]  # raw hex, block order, excludes coinbase
    transactions_weight: int

    @property
    def merkle_branches_hex(self) -> list[str]:
        return [b.hex() for b in self.merkle_branches]

    def remaining_block_weight(self) -> int:
        header_weight = (80 + len(varint(len(self.raw_transactions) + 1))) * 4
        return header_weight + _TEMP_COINBASE_WEIGHT + self.transactions_weight

    def expected_merkle_root(self, coinbase_hash: bytes) -> bytes:
        return fold_merkle_branch(coinbase_hash, self.merkle_branches)


def build_template(
    block_template: dict,
    template_id: str,
    clear_jobs: bool,
    now: float | None = None,
) -> JobTemplate:
    """Map a getblocktemplate result to a JobTemplate."""
    now = time.time() if now is None else now
    current_time = int(now)
    timestamp = max(block_template["mintime"], current_time)

    txs = block_template["transactions"]
    txids_le = [bytes.fromhex(tx["txid"])[::-1] for tx in txs]
    branches = merkle_branch(txids_le)

    # witness commitment: merkle root over wtxids (coinbase = 32 zero bytes),
    # hashed with the witness reserved value
    wtxids_le = [bytes(32)] + [bytes.fromhex(tx["hash"])[::-1] for tx in txs]
    witness_commitment = hash256(merkle_root(wtxids_le) + bytes(32))

    bits = int(block_template["bits"], 16)

    return JobTemplate(
        id=template_id,
        creation=now,
        height=block_template["height"],
        coinbasevalue=block_template["coinbasevalue"],
        network_difficulty=network_difficulty_from_bits(bits),
        clear_jobs=clear_jobs,
        version=block_template["version"],
        bits=bits,
        prev_hash_le=bytes.fromhex(block_template["previousblockhash"])[::-1],
        timestamp=timestamp,
        merkle_branches=branches,
        witness_commitment=witness_commitment,
        raw_transactions=[tx["data"] for tx in txs],
        transactions_weight=sum(tx.get("weight", 0) for tx in txs),
    )


def work_signature(block_template: dict, timestamp: int) -> str:
    """Dedupe signature answering 'did the template meaningfully change?'."""
    parts = [
        str(block_template["previousblockhash"]),
        str(block_template["version"]),
        str(block_template["bits"]),
        str(timestamp),
        str(block_template["height"]),
        str(block_template["coinbasevalue"]),
    ] + [tx.get("hash") or tx.get("txid") or tx.get("data") for tx in block_template["transactions"]]
    return "|".join(parts)
