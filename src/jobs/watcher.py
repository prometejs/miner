"""Template watcher: new-block events + 60s refresh → JobTemplate broadcasts.

Refreshes work when the chain tip advances or every 60 seconds, skipping
broadcasts when the template is unchanged (work-signature dedupe).
"""

import asyncio
import logging
import time
from typing import Awaitable, Callable

from ..rpc.bitcoin_rpc import BitcoinRpc
from .manager import JobsManager
from .template import JobTemplate, build_template, work_signature

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 60


class TemplateWatcher:
    def __init__(self, rpc: BitcoinRpc, jobs: JobsManager):
        self.rpc = rpc
        self.jobs = jobs
        self.current_template: JobTemplate | None = None
        self._last_block_height = 0
        self._last_work_signature: str | None = None
        self._subscribers: list[Callable[[JobTemplate], Awaitable[None]]] = []

    def subscribe(self, callback: Callable[[JobTemplate], Awaitable[None]]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[JobTemplate], Awaitable[None]]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def run(self) -> None:
        block_watcher = asyncio.create_task(self.rpc.watch_new_blocks())
        try:
            while True:
                # wait for either a new block or the refresh interval
                new_block_wait = asyncio.create_task(self.rpc.new_block_event.wait())
                done, pending = await asyncio.wait(
                    [new_block_wait], timeout=REFRESH_INTERVAL_SECONDS
                )
                for task in pending:
                    task.cancel()
                self.rpc.new_block_event.clear()
                if self.rpc.mining_info is None:
                    continue
                await self.refresh()
        finally:
            block_watcher.cancel()

    async def refresh(self) -> None:
        try:
            block_template = await self.rpc.get_block_template()
        except Exception as e:
            logger.error("Error getblocktemplate: %s", e)
            return

        current_height = self.rpc.mining_info["blocks"]
        clear_jobs = False
        if self._last_block_height == 0 or self._last_block_height != current_height:
            clear_jobs = True
            self._last_block_height = current_height
            logger.info("new block, height %s", current_height)

        now = time.time()
        timestamp = max(block_template["mintime"], int(now))
        signature = work_signature(block_template, timestamp)
        if not clear_jobs and signature == self._last_work_signature:
            return
        self._last_work_signature = signature

        template = build_template(
            block_template,
            template_id=self.jobs.next_template_id(),
            clear_jobs=clear_jobs,
            now=now,
        )
        self.jobs.register_template(template)
        self.current_template = template
        logger.info(
            "template %s: height=%s txs=%s clear=%s",
            template.id,
            template.height,
            len(template.raw_transactions),
            clear_jobs,
        )
        for callback in list(self._subscribers):
            try:
                await callback(template)
            except Exception:
                logger.exception("template subscriber failed")
