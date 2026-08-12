"""Address decoding → scriptPubKey, plus small serialization helpers."""

from .crypto import hash256

# --- varint ----------------------------------------------------------------

def varint(n: int) -> bytes:
    if n < 0xFD:
        return n.to_bytes(1, "little")
    if n <= 0xFFFF:
        return b"\xfd" + n.to_bytes(2, "little")
    if n <= 0xFFFFFFFF:
        return b"\xfe" + n.to_bytes(4, "little")
    return b"\xff" + n.to_bytes(8, "little")


# --- script number (BIP34 height) ------------------------------------------

def script_number_encode(n: int) -> bytes:
    """Minimal CScriptNum encoding (positive values), as bitcoinjs script.number.encode."""
    if n == 0:
        return b""
    result = bytearray()
    value = n
    while value > 0:
        result.append(value & 0xFF)
        value >>= 8
    if result[-1] & 0x80:
        result.append(0x00)
    return bytes(result)


# --- base58 ----------------------------------------------------------------

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58check_decode(s: str) -> bytes:
    num = 0
    for ch in s:
        idx = _B58_ALPHABET.find(ch)
        if idx == -1:
            raise ValueError(f"invalid base58 character {ch!r}")
        num = num * 58 + idx
    combined = num.to_bytes((num.bit_length() + 7) // 8, "big")
    # leading '1's are leading zero bytes
    pad = len(s) - len(s.lstrip("1"))
    combined = b"\x00" * pad + combined
    if len(combined) < 5:
        raise ValueError("base58 payload too short")
    payload, checksum = combined[:-4], combined[-4:]
    if hash256(payload)[:4] != checksum:
        raise ValueError("bad base58 checksum")
    return payload


# --- bech32 / bech32m (BIP 173 / BIP 350) ----------------------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_decode(bech: str) -> tuple[str, list[int], str]:
    if bech.lower() != bech and bech.upper() != bech:
        raise ValueError("mixed-case bech32")
    bech = bech.lower()
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech) or len(bech) > 90:
        raise ValueError("invalid bech32 framing")
    hrp, data_part = bech[:pos], bech[pos + 1 :]
    if any(ch not in _BECH32_CHARSET for ch in data_part):
        raise ValueError("invalid bech32 character")
    data = [_BECH32_CHARSET.find(ch) for ch in data_part]
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if polymod == 1:
        encoding = "bech32"
    elif polymod == _BECH32M_CONST:
        encoding = "bech32m"
    else:
        raise ValueError("bad bech32 checksum")
    return hrp, data[:-6], encoding


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool = True) -> list[int]:
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ValueError("invalid bech32 padding")
    return ret


# --- networks & scriptPubKey -----------------------------------------------

NETWORKS = {
    "mainnet": {"hrp": "bc", "p2pkh": 0x00, "p2sh": 0x05},
    "testnet": {"hrp": "tb", "p2pkh": 0x6F, "p2sh": 0xC4},
    "regtest": {"hrp": "bcrt", "p2pkh": 0x6F, "p2sh": 0xC4},
}

OP_DUP = 0x76
OP_HASH160 = 0xA9
OP_EQUALVERIFY = 0x88
OP_CHECKSIG = 0xAC
OP_EQUAL = 0x87
OP_RETURN = 0x6A


def address_to_script(address: str, network: str) -> bytes:
    """Decode a bitcoin address into its scriptPubKey; raises ValueError on
    invalid address or network mismatch. Supports p2pkh, p2sh, p2wpkh, p2wsh, p2tr."""
    params = NETWORKS[network]

    lowered = address.lower()
    if lowered.startswith(params["hrp"] + "1"):
        hrp, data, encoding = _bech32_decode(address)
        if hrp != params["hrp"]:
            raise ValueError(f"address hrp {hrp!r} does not match network {network}")
        if not data:
            raise ValueError("empty witness program")
        witver = data[0]
        program = bytes(_convertbits(data[1:], 5, 8, pad=False))
        if witver > 16 or len(program) < 2 or len(program) > 40:
            raise ValueError("invalid witness program")
        if witver == 0:
            if encoding != "bech32" or len(program) not in (20, 32):
                raise ValueError("invalid v0 witness program")
        else:
            if encoding != "bech32m":
                raise ValueError("witness v1+ requires bech32m")
        opcode = witver + 0x50 if witver > 0 else 0
        return bytes([opcode, len(program)]) + program

    payload = base58check_decode(address)
    version, body = payload[0], payload[1:]
    if len(body) != 20:
        raise ValueError("invalid base58 payload length")
    if version == params["p2pkh"]:
        return bytes([OP_DUP, OP_HASH160, 20]) + body + bytes([OP_EQUALVERIFY, OP_CHECKSIG])
    if version == params["p2sh"]:
        return bytes([OP_HASH160, 20]) + body + bytes([OP_EQUAL])
    raise ValueError(f"address version {version:#x} does not match network {network}")


def swap_endian_words(data: bytes) -> bytes:
    """Reverse byte order within each 4-byte word (stratum prevhash convention)."""
    out = bytearray(len(data))
    for i in range(0, len(data), 4):
        out[i] = data[i + 3]
        out[i + 1] = data[i + 2]
        out[i + 2] = data[i + 1]
        out[i + 3] = data[i]
    return bytes(out)
