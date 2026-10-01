import pytest

from miner.bitcoin.encoding import (
    address_to_script,
    script_number_encode,
    swap_endian_words,
    varint,
)


def test_varint():
    assert varint(0) == b"\x00"
    assert varint(0xFC) == b"\xfc"
    assert varint(0xFD) == b"\xfd\xfd\x00"
    assert varint(0x1234) == b"\xfd\x34\x12"
    assert varint(0x12345678) == b"\xfe\x78\x56\x34\x12"


def test_script_number_encode_bip34_heights():
    # 2442185 = 0x2543c9 → little-endian minimal, top bit clear
    assert script_number_encode(2442185) == bytes.fromhex("c94325")
    assert script_number_encode(1) == b"\x01"
    # 128 needs a padding byte (top bit set)
    assert script_number_encode(128) == bytes.fromhex("8000")
    assert script_number_encode(840000) == bytes.fromhex("40d10c")


def test_p2wpkh_bip173_vector():
    # BIP173 reference vector
    script = address_to_script("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4", "mainnet")
    assert script.hex() == "0014751e76e8199196d454941c45d1b3a323f1433bd6"


def test_p2wsh_bip173_testnet_vector():
    script = address_to_script(
        "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7", "testnet"
    )
    assert script.hex() == (
        "00201863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262"
    )


def test_p2tr_bech32m_vector():
    # BIP350 vector (witness v1)
    script = address_to_script(
        "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0", "mainnet"
    )
    assert script.hex() == (
        "512079be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"
    )


def test_p2pkh_mainnet():
    script = address_to_script("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "mainnet")
    assert script.hex() == "76a91477bff20c60e522dfaa3350c39b030a5d004e839a88ac"


def test_p2sh_mainnet():
    script = address_to_script("3P14159f73E4gFr7JterCCQh9QjiTjiZrG", "mainnet")
    assert script.hex() == "a914e9c3dd0c07aac76179ebc76a6c78d4d67c6c160a87"


def test_network_mismatch_rejected():
    with pytest.raises(ValueError):
        address_to_script("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4", "testnet")
    with pytest.raises(ValueError):
        address_to_script("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "testnet")


def test_invalid_address_rejected():
    with pytest.raises(ValueError):
        address_to_script("tb1qthisisnotvalid", "testnet")
    with pytest.raises(ValueError):
        address_to_script("notanaddress", "mainnet")


def test_swap_endian_words():
    data = bytes.fromhex("0011223344556677")
    assert swap_endian_words(data).hex() == "3322110077665544"
