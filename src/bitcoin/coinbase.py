"""Coinbase transaction construction and serialization.

Wire format:
- version 2, single input (null outpoint, sequence 0xffffffff), locktime 0
- input script: [height-len][BIP34 height][pool identifier][zero padding]
  where padding reserves TOTAL_EXTRANONCE_SIZE_BYTES (+ BIP34 alignment)
- outputs: payout(s), then OP_RETURN segwit commitment (value 0)
- witness: single 32-byte reserved value (zeros)
"""

from dataclasses import dataclass, field

from .encoding import OP_RETURN, address_to_script, script_number_encode, varint
from .crypto import hash256

EXTRANONCE1_SIZE_BYTES = 4
EXTRANONCE2_SIZE_BYTES = 8
TOTAL_EXTRANONCE_SIZE_BYTES = EXTRANONCE1_SIZE_BYTES + EXTRANONCE2_SIZE_BYTES

MAX_BLOCK_WEIGHT = 4_000_000
MAX_SCRIPT_SIZE = 100

SEGWIT_COMMITMENT_HEADER = bytes.fromhex("aa21a9ed")
WITNESS_RESERVED_VALUE = bytes(32)


@dataclass
class CoinbaseOutput:
    script: bytes
    value: int


@dataclass
class CoinbaseTransaction:
    input_script: bytes
    outputs: list[CoinbaseOutput] = field(default_factory=list)
    version: int = 2
    locktime: int = 0

    def serialize(self, include_witness: bool) -> bytes:
        parts = [self.version.to_bytes(4, "little")]
        if include_witness:
            parts.append(b"\x00\x01")  # segwit marker + flag
        parts.append(varint(1))
        parts.append(bytes(32))  # null prevout hash
        parts.append(b"\xff\xff\xff\xff")  # prevout index
        parts.append(varint(len(self.input_script)))
        parts.append(self.input_script)
        parts.append(b"\xff\xff\xff\xff")  # sequence
        parts.append(varint(len(self.outputs)))
        for out in self.outputs:
            parts.append(out.value.to_bytes(8, "little"))
            parts.append(varint(len(out.script)))
            parts.append(out.script)
        if include_witness:
            parts.append(varint(1))  # one witness item
            parts.append(varint(len(WITNESS_RESERVED_VALUE)))
            parts.append(WITNESS_RESERVED_VALUE)
        parts.append(self.locktime.to_bytes(4, "little"))
        return b"".join(parts)

    def txid_hash(self) -> bytes:
        """Internal-byte-order txid (non-witness serialization)."""
        return hash256(self.serialize(include_witness=False))

    def weight(self) -> int:
        base = len(self.serialize(include_witness=False))
        total = len(self.serialize(include_witness=True))
        return base * 3 + total


def build_input_script(height: int, pool_identifier: str) -> tuple[bytes, bytes]:
    """Returns (script_with_identifier, script_without_identifier)."""
    height_encoded = script_number_encode(height)
    height_len = bytes([len(height_encoded)])
    padding = bytes(TOTAL_EXTRANONCE_SIZE_BYTES + (3 - len(height_encoded)))
    with_id = height_len + height_encoded + pool_identifier.encode() + padding
    without_id = height_len + height_encoded + padding
    return with_id, without_id


def build_coinbase(
    payouts: list[tuple[str, float]],
    reward: int,
    height: int,
    witness_commitment: bytes,
    pool_identifier: str,
    network: str,
    remaining_block_weight: int,
) -> CoinbaseTransaction:
    """Build the coinbase transaction for a job.

    `payouts` is a list of (address, percent). Floor rounding remainders are
    added to the first output.
    """
    outputs: list[CoinbaseOutput] = []
    reward_balance = reward
    for address, percent in payouts:
        amount = int((percent / 100) * reward)  # Math.floor for non-negative values
        reward_balance -= amount
        outputs.append(CoinbaseOutput(script=address_to_script(address, network), value=amount))
    outputs[0].value += reward_balance

    script_with_id, script_without_id = build_input_script(height, pool_identifier)
    script = script_with_id
    if len(script) > MAX_SCRIPT_SIZE:
        script = script_without_id

    commitment_script = bytes([OP_RETURN, 0x24]) + SEGWIT_COMMITMENT_HEADER + witness_commitment
    outputs.append(CoinbaseOutput(script=commitment_script, value=0))

    tx = CoinbaseTransaction(input_script=script, outputs=outputs)

    if tx.weight() + remaining_block_weight > MAX_BLOCK_WEIGHT:
        tx.input_script = script_without_id

    return tx
