"""End-to-end session test: replay a recorded real-miner session through a
live StratumSession with a fixture template."""

import asyncio
import json
import pathlib

import pytest

from miner.config import Settings
from miner.jobs.manager import JobsManager
from miner.jobs.template import build_template
from miner.jobs.watcher import TemplateWatcher
from miner.quanta.allocator import QuantaAllocator
from miner.quanta.ledger import QuantaLedger
from miner.stratum.session import StratumSession

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
RECORDING = json.loads((FIXTURES / "mock_recording_1.json").read_text())
GOLDEN = json.loads((FIXTURES / "golden_vectors.json").read_text())


class FakeWriter:
    def __init__(self):
        self.lines: list[str] = []
        self.closed = False

    def write(self, data: bytes):
        self.lines.append(data.decode())

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    def get_extra_info(self, _name):
        return ("test", 0)


class FakeRpc:
    def __init__(self):
        self.submitted: list[str] = []

    async def submit_block(self, block_hex: str):
        self.submitted.append(block_hex)
        return None


class FixedAllocator(QuantaAllocator):
    def allocate(self) -> str:
        return RECORDING["extranonce1"]

    def release(self, extranonce1: str) -> None:
        pass


def make_session():
    settings = Settings(network="testnet", pool_identifier="Public-Pool")
    jobs = JobsManager()
    rpc = FakeRpc()
    watcher = TemplateWatcher(rpc, jobs)  # type: ignore[arg-type]
    template = build_template(
        RECORDING["block_template"],
        template_id=jobs.next_template_id(),
        clear_jobs=True,
        now=int(RECORDING["time_hex"], 16),
    )
    jobs.register_template(template)
    watcher.current_template = template

    writer = FakeWriter()
    session = StratumSession(
        reader=None,  # driving handle_message directly
        writer=writer,
        settings=settings,
        jobs=jobs,
        watcher=watcher,
        rpc=rpc,  # type: ignore[arg-type]
        allocator=FixedAllocator(),
        ledger=QuantaLedger(),
    )
    return session, writer, rpc


async def replay_handshake(session):
    await session.handle_message('{"id": 1, "method": "mining.subscribe", "params": ["bitaxe v2.2"]}')
    await session.handle_message('{"id": 4, "method": "mining.suggest_difficulty", "params": [0]}')
    await session.handle_message(
        '{"id": 3, "method": "mining.authorize",'
        ' "params": ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.bitaxe3", "x"]}'
    )


def test_full_session_replay_accepts_recorded_share():
    async def scenario():
        session, writer, rpc = make_session()
        await replay_handshake(session)

        # subscribe response with the pinned extranonce1, exact wire format
        assert (
            writer.lines[0]
            == '{"id":1,"error":null,"result":[[["mining.notify","57a6f098"]],"57a6f098",8]}\n'
        )
        # the notify sent on init must match the golden vectors byte-for-byte
        notifies = [l for l in writer.lines if '"method":"mining.notify"' in l]
        assert notifies[0] == GOLDEN["notifyLine"]

        # replay the recorded submit; session difficulty is 0 → accepted
        await session.handle_message(
            '{"id": 5, "method": "mining.submit", "params":'
            ' ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.bitaxe3", "1",'
            ' "c708000000000000", "64b3f3ec", "ed460d91", "00002000"]}'
        )
        assert writer.lines[-1] == '{"id":5,"error":null,"result":true}\n'

        # duplicate share → error 22
        await session.handle_message(
            '{"id": 6, "method": "mining.submit", "params":'
            ' ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.bitaxe3", "1",'
            ' "c708000000000000", "64b3f3ec", "ed460d91", "00002000"]}'
        )
        assert json.loads(writer.lines[-1])["error"][0] == 22

        # unknown job → error 21
        await session.handle_message(
            '{"id": 7, "method": "mining.submit", "params":'
            ' ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.bitaxe3", "ff",'
            ' "c708000000000001", "64b3f3ec", "ed460d91", "00002000"]}'
        )
        assert json.loads(writer.lines[-1])["error"][0] == 21

        # quanta ledger recorded the accepted + rejected shares
        record = session.ledger.quanta["57a6f098"]
        assert record.accepted_shares == 1
        assert record.rejected_shares == 2
        assert record.address == "tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4"
        await session.destroy()

    asyncio.run(scenario())


def test_low_difficulty_share_rejected():
    async def scenario():
        session, writer, _ = make_session()
        await session.handle_message('{"id": 1, "method": "mining.subscribe", "params": ["bitaxe v2.2"]}')
        await session.handle_message(
            '{"id": 3, "method": "mining.authorize",'
            ' "params": ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.bitaxe3", "x"]}'
        )
        # no suggested difficulty → session difficulty 100000; recorded share is far below
        await session.handle_message(
            '{"id": 5, "method": "mining.submit", "params":'
            ' ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.bitaxe3", "1",'
            ' "c708000000000000", "64b3f3ec", "ed460d91", "00002000"]}'
        )
        assert writer.lines[-1] == '{"id":5,"result":null,"error":[23,"Difficulty too low",""]}\n'
        await session.destroy()

    asyncio.run(scenario())


def test_submit_before_init_closes_connection():
    async def scenario():
        session, writer, _ = make_session()
        await session.handle_message(
            '{"id": 5, "method": "mining.submit", "params":'
            ' ["a.b", "1", "c708000000000000", "64b3f3ec", "ed460d91", "00002000"]}'
        )
        assert session._closed
        assert writer.closed

    asyncio.run(scenario())


def test_cpuminer_gets_low_starting_difficulty():
    async def scenario():
        session, writer, _ = make_session()
        await session.handle_message('{"id": 1, "method": "mining.subscribe", "params": ["cpuminer/2.5.1"]}')
        await session.handle_message(
            '{"id": 3, "method": "mining.authorize",'
            ' "params": ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4", "x"]}'
        )
        assert session.session_difficulty == 0.1
        assert '{"id":null,"method":"mining.set_difficulty","params":[0.1]}\n' in writer.lines
        await session.destroy()

    asyncio.run(scenario())


def test_starting_diff_from_password():
    async def scenario():
        session, writer, _ = make_session()
        await session.handle_message('{"id": 1, "method": "mining.subscribe", "params": ["bitaxe v2.2"]}')
        await session.handle_message(
            '{"id": 3, "method": "mining.authorize",'
            ' "params": ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4", "d=500000"]}'
        )
        assert session.session_difficulty == 500000
        await session.destroy()

    asyncio.run(scenario())


def test_version_rolling_negotiation():
    async def scenario():
        session, writer, _ = make_session()
        await session.handle_message(
            '{"id": 2, "method": "mining.configure",'
            ' "params": [["version-rolling"], {"version-rolling.mask": "ffffffff"}]}'
        )
        result = json.loads(writer.lines[-1])
        assert result["result"]["version-rolling"] is True
        assert result["result"]["version-rolling.mask"] == "1fffe000"
        await session.destroy()

    asyncio.run(scenario())
