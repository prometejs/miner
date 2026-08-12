"""Golden-vector regression tests: the wire format is frozen.

tests/fixtures/golden_vectors.json captures every derived byte for a recorded
real-miner session — template fields, merkle branches, witness commitment,
coinb1/coinb2, the full notify line, the 80-byte header for a recorded share,
and the submitblock hex. Any change to work construction that alters these
bytes is a wire-format break and must be deliberate (regenerate the fixture in
its own commit and explain why).
"""

import json
import pathlib

import pytest

from prometejs_miner.jobs.mining_job import MiningJob
from prometejs_miner.jobs.template import build_template

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
GOLDEN = json.loads((FIXTURES / "golden_vectors.json").read_text())
RECORDING = json.loads((FIXTURES / "mock_recording_1.json").read_text())


@pytest.fixture()
def template():
    return build_template(
        RECORDING["block_template"],
        template_id="1",
        clear_jobs=True,
        now=int(RECORDING["time_hex"], 16),
    )


@pytest.fixture()
def job(template):
    return MiningJob(
        job_id="1",
        payouts=[(GOLDEN["address"], 100)],
        template=template,
        pool_identifier="Public-Pool",
        network="testnet",
    )


def test_template_fields_match_golden(template):
    up = GOLDEN["template"]
    assert template.timestamp == up["timestamp"]
    assert template.version == up["version"]
    assert template.bits == up["bits"]
    assert template.prev_hash_le.hex() == up["prevHashLE"]
    assert template.witness_commitment.hex() == up["witnessCommit"]
    assert template.network_difficulty == pytest.approx(up["networkDifficulty"])


def test_merkle_branches_match_golden(template):
    assert template.merkle_branches_hex == GOLDEN["template"]["merkleBranches"]


def test_coinbase_parts_match_golden(job):
    assert job.coinbase_part1.hex() == GOLDEN["coinb1"]
    assert job.coinbase_part2.hex() == GOLDEN["coinb2"]


def test_notify_line_matches_golden(job, template):
    assert job.notify_payload(template) == GOLDEN["notifyLine"]


def test_header_matches_golden(job, template):
    sub = GOLDEN["submit"]
    header = job.build_header(
        template,
        version_mask=int(sub["versionMask"], 16),
        nonce=int(sub["nonce"], 16),
        extranonce1=GOLDEN["extranonce1"],
        extranonce2=sub["extraNonce2"],
        timestamp=int(sub["ntime"], 16),
    )
    assert header.hex() == GOLDEN["headerHex"]


def test_block_hex_matches_golden(job, template):
    sub = GOLDEN["submit"]
    block_hex = job.build_block_hex(
        template,
        version_mask=int(sub["versionMask"], 16),
        nonce=int(sub["nonce"], 16),
        extranonce1=GOLDEN["extranonce1"],
        extranonce2=sub["extraNonce2"],
        timestamp=int(sub["ntime"], 16),
    )
    assert block_hex == GOLDEN["blockHex"]
