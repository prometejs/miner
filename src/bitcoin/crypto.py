import hashlib


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def hash256(data: bytes) -> bytes:
    """Bitcoin's double SHA-256."""
    return sha256(sha256(data))


def merkle_root(hashes: list[bytes]) -> bytes:
    """Standard bitcoin merkle root over internal-byte-order hashes."""
    if not hashes:
        raise ValueError("merkle_root of empty list")
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [hash256(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_branch(txids_le: list[bytes]) -> list[bytes]:
    """Stratum merkle branch for the coinbase (position 0).

    `txids_le` are the internal-byte-order txids of all NON-coinbase
    transactions, in block order. The branch folds with
    `fold_merkle_branch(coinbase_hash, branch)` to the block merkle root.
    """
    branch: list[bytes] = []
    level: list[bytes | None] = [None] + list(txids_le)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        branch.append(level[1])  # sibling of the position-0 element
        next_level: list[bytes | None] = [None]
        for i in range(2, len(level), 2):
            next_level.append(hash256(level[i] + level[i + 1]))
        level = next_level
    return branch


def fold_merkle_branch(coinbase_hash: bytes, branch: list[bytes]) -> bytes:
    root = coinbase_hash
    for sibling in branch:
        root = hash256(root + sibling)
    return root
