"""Per-client mining job: coinbase split, notify payload, header/block build.

The coinbase pays the authorizing miner's own address (solo, non-custodial),
so a job instance is client-specific.
"""

import json
import struct
import time

from ..bitcoin.coinbase import (
    TOTAL_EXTRANONCE_SIZE_BYTES,
    CoinbaseTransaction,
    build_coinbase,
)
from ..bitcoin.crypto import hash256
from ..bitcoin.encoding import swap_endian_words, varint
from .template import JobTemplate


class MiningJob:
    def __init__(
        self,
        job_id: str,
        payouts: list[tuple[str, float]],
        template: JobTemplate,
        pool_identifier: str,
        network: str,
    ):
        self.job_id = job_id
        self.job_template_id = template.id
        self.network_difficulty = template.network_difficulty
        self.creation = time.time()

        self.coinbase: CoinbaseTransaction = build_coinbase(
            payouts=payouts,
            reward=template.coinbasevalue,
            height=template.height,
            witness_commitment=template.witness_commitment,
            pool_identifier=pool_identifier,
            network=network,
            remaining_block_weight=template.remaining_block_weight(),
        )

        serialized = self.coinbase.serialize(include_witness=False)
        # layout: version(4) + varint(1 input)(1) + prevout(36) + script_len(1) + script
        script_len = len(self.coinbase.input_script)
        part_one_end = 4 + 1 + 36 + len(varint(script_len)) + script_len
        self.coinbase_part1: bytes = serialized[: part_one_end - TOTAL_EXTRANONCE_SIZE_BYTES]
        self.coinbase_part2: bytes = serialized[part_one_end:]

    def coinbase_with_nonces(self, extranonce1: str, extranonce2: str) -> bytes:
        return self.coinbase_part1 + bytes.fromhex(extranonce1 + extranonce2) + self.coinbase_part2

    def build_header(
        self,
        template: JobTemplate,
        version_mask: int,
        nonce: int,
        extranonce1: str,
        extranonce2: str,
        timestamp: int,
    ) -> bytes:
        coinbase_hash = hash256(self.coinbase_with_nonces(extranonce1, extranonce2))
        merkle_root = template.expected_merkle_root(coinbase_hash)

        version = template.version
        if version_mask:
            version ^= version_mask

        return b"".join(
            [
                struct.pack("<I", version & 0xFFFFFFFF),
                template.prev_hash_le,
                merkle_root,
                struct.pack("<I", timestamp),
                struct.pack("<I", template.bits),
                struct.pack("<I", nonce & 0xFFFFFFFF),
            ]
        )

    def build_block_hex(
        self,
        template: JobTemplate,
        version_mask: int,
        nonce: int,
        extranonce1: str,
        extranonce2: str,
        timestamp: int,
    ) -> str:
        """Full block for submitblock: header + txcount + witness-serialized txs."""
        header = self.build_header(template, version_mask, nonce, extranonce1, extranonce2, timestamp)

        final_coinbase = CoinbaseTransaction(
            input_script=self.coinbase.input_script[:-TOTAL_EXTRANONCE_SIZE_BYTES]
            + bytes.fromhex(extranonce1 + extranonce2),
            outputs=self.coinbase.outputs,
        )

        parts = [header, varint(len(template.raw_transactions) + 1)]
        parts.append(final_coinbase.serialize(include_witness=True))
        block_hex = b"".join(parts).hex()
        return block_hex + "".join(template.raw_transactions)

    def notify_payload(self, template: JobTemplate) -> str:
        """mining.notify line (compact JSON, no spaces — the stratum wire convention)."""
        message = {
            "id": None,
            "method": "mining.notify",
            "params": [
                self.job_id,
                swap_endian_words(template.prev_hash_le).hex(),
                self.coinbase_part1.hex(),
                self.coinbase_part2.hex(),
                template.merkle_branches_hex,
                format(template.version, "x"),
                format(template.bits, "x"),
                format(template.timestamp, "x"),
                template.clear_jobs,
            ],
        }
        return json.dumps(message, separators=(",", ":")) + "\n"
