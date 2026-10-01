"""Full-stack smoke test: real TCP stratum server + HTTP API over sockets,
with the RPC layer faked to serve the fixture template."""

import asyncio
import json
import pathlib

import aiohttp

from miner.config import Settings
from miner.jobs.template import build_template
from miner.stratum.server import StratumServer

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
RECORDING = json.loads((FIXTURES / "mock_recording_1.json").read_text())

STRATUM_PORT = 13333
API_PORT = 13334


def test_server_end_to_end():
    async def scenario():
        settings = Settings(
            network="testnet",
            stratum_port=STRATUM_PORT,
            api_port=API_PORT,
            pool_identifier="Public-Pool",
        )
        server = StratumServer(settings)

        # fake out the node-facing edges
        async def fake_start():
            pass

        async def fake_submit(block_hex):
            return None

        server.rpc.start = fake_start
        server.rpc.close = fake_start
        server.rpc.submit_block = fake_submit

        template = build_template(
            RECORDING["block_template"],
            template_id=server.jobs.next_template_id(),
            clear_jobs=True,
            now=int(RECORDING["time_hex"], 16),
        )
        server.jobs.register_template(template)
        server.watcher.current_template = template

        async def fake_watch():
            await asyncio.sleep(3600)

        server.watcher.run = fake_watch

        serve_task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.3)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", STRATUM_PORT)

            async def send(line: str) -> None:
                writer.write((line + "\n").encode())
                await writer.drain()

            async def recv() -> dict:
                return json.loads(await asyncio.wait_for(reader.readline(), timeout=2))

            await send('{"id": 1, "method": "mining.subscribe", "params": ["bitaxe v2.2"]}')
            sub = await recv()
            extranonce1 = sub["result"][1]
            assert len(extranonce1) == 8
            assert sub["result"][2] == 8

            await send(
                '{"id": 2, "method": "mining.configure",'
                ' "params": [["version-rolling"], {"version-rolling.mask": "ffffffff"}]}'
            )
            conf = await recv()
            assert conf["result"]["version-rolling.mask"] == "1fffe000"

            await send('{"id": 4, "method": "mining.suggest_difficulty", "params": [0]}')
            set_diff = await recv()
            assert set_diff["method"] == "mining.set_difficulty"

            await send(
                '{"id": 3, "method": "mining.authorize",'
                ' "params": ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.smoke", "x"]}'
            )
            auth = await recv()
            assert auth["result"] is True

            notify = await recv()
            assert notify["method"] == "mining.notify"
            job_id = notify["params"][0]

            # a submit against the live-assigned extranonce1 (any diff, since 0)
            await send(
                json.dumps(
                    {
                        "id": 5,
                        "method": "mining.submit",
                        "params": [
                            "tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.smoke",
                            job_id,
                            "c708000000000000",
                            RECORDING["time_hex"],
                            "ed460d91",
                            "00002000",
                        ],
                    }
                )
            )
            result = await recv()
            assert result == {"id": 5, "error": None, "result": True}

            # API reflects the live session and the accepted share
            async with aiohttp.ClientSession() as http:
                async with http.get(f"http://127.0.0.1:{API_PORT}/info") as resp:
                    info = await resp.json()
                assert info["connectedSessions"] == 1
                assert info["currentTemplate"]["height"] == RECORDING["block_template"]["height"]

                async with http.get(f"http://127.0.0.1:{API_PORT}/quanta") as resp:
                    quanta = await resp.json()
                assert quanta["totals"]["acceptedShares"] == 1
                mine = [q for q in quanta["quanta"] if q["extranonce1"] == extranonce1]
                assert mine and mine[0]["worker"] == "smoke"

            writer.close()
        finally:
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(scenario())
