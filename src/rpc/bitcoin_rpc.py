"""Bitcoin Core JSON-RPC client + new-block watcher (ZMQ or polling)."""

import asyncio
import logging
import pathlib

import aiohttp

from ..config import Settings

logger = logging.getLogger(__name__)


class BitcoinRpc:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._request_id = 0
        self._session: aiohttp.ClientSession | None = None
        self._block_height = 0
        self.new_block_event: asyncio.Event = asyncio.Event()
        self.mining_info: dict | None = None

    async def start(self) -> None:
        user = self.settings.bitcoin_rpc_user
        password = self.settings.bitcoin_rpc_password
        cookiefile = self.settings.bitcoin_rpc_cookiefile
        if cookiefile:
            user, password = pathlib.Path(cookiefile).read_text().strip().split(":", 1)
        self._session = aiohttp.ClientSession(
            base_url=self.settings.rpc_base_url,
            auth=aiohttp.BasicAuth(user, password),
            timeout=aiohttp.ClientTimeout(total=self.settings.bitcoin_rpc_timeout),
        )
        try:
            await self.call("getrpcinfo")
            logger.info("Bitcoin RPC connected")
        except Exception:
            logger.error("Could not reach RPC host")

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def call(self, method: str, params: list | None = None):
        self._request_id += 1
        async with self._session.post(
            "/", json={"jsonrpc": "1.0", "id": self._request_id, "method": method, "params": params or []}
        ) as resp:
            data = await resp.json(content_type=None)
        if data.get("error") is not None:
            raise RuntimeError(f"RPC {method} error: {data['error']}")
        return data["result"]

    async def get_mining_info(self) -> dict | None:
        try:
            return await self.call("getmininginfo")
        except Exception as e:
            logger.error("Error getmininginfo: %s", e)
            return None

    async def get_block_template(self) -> dict:
        template = None
        while template is None:
            template = await self.call(
                "getblocktemplate",
                [{"rules": ["segwit"], "mode": "template", "capabilities": ["serverlist", "proposal"]}],
            )
        return template

    async def submit_block(self, block_hex: str) -> str | None:
        """Returns None on success (bitcoind convention), error string otherwise."""
        try:
            response = await self.call("submitblock", [block_hex])
            logger.info("BLOCK SUBMISSION RESPONSE: %s", response if response is not None else "SUCCESS!")
            return response
        except Exception as e:
            logger.error("BLOCK SUBMISSION ERROR: %s", e)
            return str(e)

    async def poll_mining_info(self) -> None:
        mining_info = await self.get_mining_info()
        if mining_info is not None and mining_info["blocks"] > self._block_height:
            logger.info("block height change: %s", mining_info["blocks"])
            self.mining_info = mining_info
            self._block_height = mining_info["blocks"]
            self.new_block_event.set()

    async def watch_new_blocks(self) -> None:
        """Sets new_block_event whenever the chain tip advances.

        With ZMQ configured, listens for rawblock notifications; otherwise
        polls getmininginfo every 500ms.
        """
        if self.settings.bitcoin_zmq_host:
            import zmq
            import zmq.asyncio

            ctx = zmq.asyncio.Context.instance()
            sock = ctx.socket(zmq.SUB)
            sock.connect(self.settings.bitcoin_zmq_host)
            sock.setsockopt_string(zmq.SUBSCRIBE, "rawblock")
            logger.info("Using ZMQ at %s", self.settings.bitcoin_zmq_host)
            await self.poll_mining_info()
            while True:
                await sock.recv_multipart()
                logger.info("ZMQ new block")
                await self.poll_mining_info()
        else:
            while True:
                await self.poll_mining_info()
                await asyncio.sleep(0.5)
