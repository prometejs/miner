"""Difficulty math (float semantics chosen to match cgminer-lineage pools)."""

# cgminer-lineage constant: the difficulty-1 target expressed as a double.
TRUE_DIFF_ONE = 2.695953529101131e67


def network_difficulty_from_bits(nbits: int) -> float:
    """nBits (compact target) to network difficulty; float math is intentional."""
    mantissa = nbits & 0x007FFFFF
    exponent = (nbits >> 24) & 0xFF
    target = mantissa * (256.0 ** (exponent - 3))
    max_target = (2.0 ** 208) * 65535
    return max_target / target


def le256_to_double(data: bytes) -> float:
    """Interpret a 32-byte hash as a little-endian number, as a float."""
    number = 0.0
    for i in range(len(data) - 1, -1, -1):
        number = number * 256 + data[i]
    return number


def share_difficulty(header_hash: bytes) -> float:
    value = le256_to_double(header_hash)
    if value == 0:
        return float("inf")
    return TRUE_DIFF_ONE / value
