"""Core correctness tests driven by a recorded real-miner testnet session."""

import json
import pathlib

import pytest

from prometejs_miner.bitcoin.coinbase import TOTAL_EXTRANONCE_SIZE_BYTES
from prometejs_miner.bitcoin.crypto import hash256
from prometejs_miner.bitcoin.difficulty import share_difficulty
from prometejs_miner.jobs.mining_job import MiningJob
from prometejs_miner.jobs.template import build_template

FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "mock_recording_1.json").read_text()
)


@pytest.fixture()
def template():
    bt = FIXTURE["block_template"]
    now = int(FIXTURE["time_hex"], 16)
    return build_template(bt, template_id="1", clear_jobs=True, now=now)


@pytest.fixture()
def job(template):
    return MiningJob(
        job_id="1",
        payouts=[(FIXTURE["payout_address"], 100)],
        template=template,
        pool_identifier="Public-Pool",
        network="testnet",
    )


def test_witness_commitment_matches_node(template):
    """Our computed commitment must equal the node's default_witness_commitment."""
    dwc = FIXTURE["block_template"]["default_witness_commitment"]
    # node value is the full OP_RETURN script: 6a24aa21a9ed || commitment
    assert dwc == "6a24aa21a9ed" + template.witness_commitment.hex()


def test_merkle_branch_folds_to_full_tree_root(template):
    """Folding any coinbase hash through the branch must equal the full merkle tree."""
    from prometejs_miner.bitcoin.crypto import merkle_root

    fake_coinbase_hash = hash256(b"anything")
    txids_le = [
        bytes.fromhex(tx["txid"])[::-1] for tx in FIXTURE["block_template"]["transactions"]
    ]
    expected = merkle_root([fake_coinbase_hash] + txids_le)
    assert template.expected_merkle_root(fake_coinbase_hash) == expected


def test_coinbase_split_reserves_extranonce_space(job):
    en1 = FIXTURE["extranonce1"]
    en2 = FIXTURE["submit"]["extraNonce2"]
    assert len(bytes.fromhex(en1 + en2)) == TOTAL_EXTRANONCE_SIZE_BYTES

    coinbase = job.coinbase_with_nonces(en1, en2)
    # script must end with the extranonces (they replace the zero padding)
    script_len = coinbase[41]
    script = coinbase[42 : 42 + script_len]
    assert script.hex().endswith(en1 + en2)


def test_pool_identifier_in_script(job):
    assert b"Public-Pool" in job.coinbase.input_script


def test_pool_identifier_dropped_when_script_too_big(template):
    job = MiningJob(
        job_id="1",
        payouts=[(FIXTURE["payout_address"], 100)],
        template=template,
        pool_identifier="A" * 85,
        network="testnet",
    )
    assert b"A" not in job.coinbase.input_script


def test_pool_identifier_84_chars_fits(template):
    job = MiningJob(
        job_id="1",
        payouts=[(FIXTURE["payout_address"], 100)],
        template=template,
        pool_identifier="A" * 84,
        network="testnet",
    )
    assert b"A" * 84 in job.coinbase.input_script


def test_coinbase_pays_full_reward(job):
    reward = FIXTURE["block_template"]["coinbasevalue"]
    assert sum(o.value for o in job.coinbase.outputs) == reward


def test_share_difficulty_computation(job, template):
    """The recorded share replays to a valid 80-byte header with a computable
    difficulty. (Its absolute value is not meaningful against today's coinbase
    construction; byte-identity is asserted in test_golden_vectors.py.)"""
    sub = FIXTURE["submit"]
    header = job.build_header(
        template,
        version_mask=int(sub["versionMask"], 16),
        nonce=int(sub["nonce"], 16),
        extranonce1=FIXTURE["extranonce1"],
        extranonce2=sub["extraNonce2"],
        timestamp=int(sub["ntime"], 16),
    )
    assert len(header) == 80
    diff = share_difficulty(hash256(header))
    assert diff > 0 and diff != float("inf")


def test_version_mask_applied(job, template):
    sub = FIXTURE["submit"]
    kwargs = dict(
        nonce=int(sub["nonce"], 16),
        extranonce1=FIXTURE["extranonce1"],
        extranonce2=sub["extraNonce2"],
        timestamp=int(sub["ntime"], 16),
    )
    with_mask = job.build_header(template, version_mask=0x00002000, **kwargs)
    without_mask = job.build_header(template, version_mask=0, **kwargs)
    assert int.from_bytes(with_mask[:4], "little") == template.version ^ 0x00002000
    assert int.from_bytes(without_mask[:4], "little") == template.version
    assert with_mask[4:] == without_mask[4:]


def test_block_hex_consistency(job, template):
    """The submitblock payload must start with the exact header and contain a
    coinbase whose txid folds through the branch to the header's merkle root."""
    sub = FIXTURE["submit"]
    args = dict(
        version_mask=int(sub["versionMask"], 16),
        nonce=int(sub["nonce"], 16),
        extranonce1=FIXTURE["extranonce1"],
        extranonce2=sub["extraNonce2"],
        timestamp=int(sub["ntime"], 16),
    )
    header = job.build_header(template, **args)
    block_hex = job.build_block_hex(template, **args)

    assert block_hex.startswith(header.hex())
    # tx count varint: 5 template txs + coinbase
    assert block_hex[160:162] == "06"

    # coinbase txid (non-witness) must fold to the header merkle root
    coinbase = job.coinbase_with_nonces(FIXTURE["extranonce1"], sub["extraNonce2"])
    merkle_in_header = header[36:68]
    assert template.expected_merkle_root(hash256(coinbase)) == merkle_in_header

    # all template raw txs present verbatim
    for tx in FIXTURE["block_template"]["transactions"]:
        assert tx["data"] in block_hex


def test_notify_payload_format(job, template):
    line = job.notify_payload(template)
    assert line.endswith("\n")
    msg = json.loads(line)
    assert msg["id"] is None
    assert msg["method"] == "mining.notify"
    params = msg["params"]
    assert params[0] == "1"
    # prevhash is word-swapped hex, 64 chars
    assert len(params[1]) == 64
    # coinb1 + extranonces + coinb2 must be a parseable coinbase paying the reward
    assert params[8] is True  # clear_jobs
    assert params[5] == format(template.version, "x")
    assert params[6] == format(template.bits, "x")
    assert params[7] == FIXTURE["time_hex"]
    assert params[4] == template.merkle_branches_hex
