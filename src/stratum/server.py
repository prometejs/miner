"""TCP server binding sessions to shared state, plus a minimal JSON status API."""

import asyncio
import json
import logging
import time

from aiohttp import web

from ..config import Settings
from ..jobs.manager import JobsManager
from ..jobs.watcher import TemplateWatcher
from ..quanta.allocator import QuantaAllocator
from ..quanta.ledger import QuantaLedger
from ..rpc.bitcoin_rpc import BitcoinRpc
from .session import StratumSession

logger = logging.getLogger(__name__)


class StratumServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.rpc = BitcoinRpc(settings)
        self.jobs = JobsManager()
        self.watcher = TemplateWatcher(self.rpc, self.jobs)
        self.allocator = QuantaAllocator(settings.quanta_policy)
        self.ledger = QuantaLedger()
        self.sessions: set[StratumSession] = set()
        self.blocks_found: list[dict] = []
        self.started_at = time.time()

    async def _on_block_found(self, **kwargs) -> None:
        self.blocks_found.append({**kwargs, "time": time.time()})

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session = StratumSession(
            reader,
            writer,
            settings=self.settings,
            jobs=self.jobs,
            watcher=self.watcher,
            rpc=self.rpc,
            allocator=self.allocator,
            ledger=self.ledger,
            on_block_found=self._on_block_found,
        )
        self.sessions.add(session)
        try:
            await session.run()
        finally:
            self.sessions.discard(session)

    # --- status API --------------------------------------------------------

    def _api_app(self) -> web.Application:
        async def info(_request):
            template = self.watcher.current_template
            return web.json_response(
                {
                    "pool": self.settings.pool_identifier,
                    "network": self.settings.network,
                    "uptime": time.time() - self.started_at,
                    "connectedSessions": len(self.sessions),
                    "currentTemplate": None
                    if template is None
                    else {
                        "id": template.id,
                        "height": template.height,
                        "txCount": len(template.raw_transactions),
                        "networkDifficulty": template.network_difficulty,
                        "timestamp": template.timestamp,
                    },
                    "blocksFound": len(self.blocks_found),
                }
            )

        async def quanta(_request):
            return web.json_response(self.ledger.snapshot())

        async def blocks(_request):
            return web.json_response(
                [
                    {k: v for k, v in b.items() if k != "block_hex"}
                    for b in self.blocks_found
                ]
            )

        app = web.Application()
        app.router.add_get("/info", info)
        app.router.add_get("/quanta", quanta)
        app.router.add_get("/blocks", blocks)
        return app

    async def serve(self) -> None:
        await self.rpc.start()

        watcher_task = asyncio.create_task(self.watcher.run())

        server = await asyncio.start_server(
            self._handle_connection, host="0.0.0.0", port=self.settings.stratum_port
        )
        logger.info("stratum listening on :%s", self.settings.stratum_port)

        runner = web.AppRunner(self._api_app())
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.settings.api_port)
        await site.start()
        logger.info("api listening on :%s", self.settings.api_port)

        try:
            async with server:
                await server.serve_forever()
        finally:
            watcher_task.cancel()
            await runner.cleanup()
            await self.rpc.close()
