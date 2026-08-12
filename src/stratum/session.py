"""Per-connection stratum session.

Flow: subscribe assigns extranonce1; after subscribe +
authorize the session initializes (initial set_difficulty, job broadcasts,
60s vardiff check). Submissions are validated against the shared job/template
registries; a share meeting network difficulty is submitted as a block.
"""

import asyncio
import json
import logging
import time

from ..bitcoin.crypto import hash256
from ..bitcoin.difficulty import share_difficulty
from ..config import Settings
from ..jobs.manager import JobsManager
from ..jobs.mining_job import MiningJob
from ..jobs.template import JobTemplate
from ..jobs.watcher import TemplateWatcher
from ..quanta.allocator import QuantaAllocator
from ..quanta.ledger import QuantaLedger
from ..rpc.bitcoin_rpc import BitcoinRpc
from . import messages
from .vardiff import SessionStatistics

logger = logging.getLogger(__name__)

DIFFICULTY_CHECK_INTERVAL = 60
FIFTY_TH = 50_000_000_000_000


class StratumSession:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        settings: Settings,
        jobs: JobsManager,
        watcher: TemplateWatcher,
        rpc: BitcoinRpc,
        allocator: QuantaAllocator,
        ledger: QuantaLedger,
        on_block_found=None,
    ):
        self.reader = reader
        self.writer = writer
        self.settings = settings
        self.jobs = jobs
        self.watcher = watcher
        self.rpc = rpc
        self.allocator = allocator
        self.ledger = ledger
        self.on_block_found = on_block_found

        self.extranonce1: str | None = None
        self.subscription: messages.Subscribe | None = None
        self.authorization: messages.Authorize | None = None
        self.session_difficulty: float = 100000
        self.used_suggested_difficulty = False
        self.suggested_difficulty: float | None = None
        self.statistics = SessionStatistics()
        self.initialized = False
        self.session_start: float | None = None
        self._submission_hashes: set[str] = set()
        self._background: list[asyncio.Task] = []
        self._job_callback = None
        self._closed = False

    # --- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        try:
            while not self._closed:
                try:
                    line = await self.reader.readline()
                except (ConnectionResetError, asyncio.IncompleteReadError):
                    break
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                try:
                    await self.handle_message(text)
                except Exception:
                    logger.exception("error handling message from %s", self.extranonce1)
                    break
        finally:
            await self.destroy()

    async def destroy(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._job_callback:
            self.watcher.unsubscribe(self._job_callback)
        for task in self._background:
            task.cancel()
        if self.extranonce1:
            self.allocator.release(self.extranonce1)
            self.ledger.close(self.extranonce1)
        try:
            self.writer.close()
        except Exception:
            pass

    async def write(self, data: str) -> bool:
        if self._closed:
            return False
        try:
            self.writer.write(data.encode())
            await self.writer.drain()
            return True
        except (ConnectionResetError, BrokenPipeError, OSError):
            await self.destroy()
            return False

    # --- message dispatch --------------------------------------------------

    async def handle_message(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            await self.destroy()
            return
        method = msg.get("method")

        try:
            if method == "mining.subscribe":
                await self._handle_subscribe(messages.Subscribe.parse(msg))
            elif method == "mining.configure":
                await self._handle_configure(messages.Configure.parse(msg))
            elif method == "mining.authorize":
                await self._handle_authorize(messages.Authorize.parse(msg, self.settings.network))
            elif method == "mining.suggest_difficulty":
                await self._handle_suggest_difficulty(messages.SuggestDifficulty.parse(msg))
            elif method == "mining.submit":
                if not self.initialized:
                    logger.info("submit before initialized")
                    await self.destroy()
                    return
                await self._handle_submit(messages.Submit.parse(msg))
        except messages.MessageError as e:
            await self.write(messages.error_line(e.msg_id, messages.OTHER_UNKNOWN, str(e)))
            if method == "mining.submit":
                await self.destroy()
            return

        if self.subscription and self.authorization and not self.initialized:
            await self._init_stratum()

    async def _handle_subscribe(self, sub: messages.Subscribe) -> None:
        if self.session_start is None:
            self.session_start = time.time()
            self.extranonce1 = self.allocator.allocate()
            peer = self.writer.get_extra_info("peername")
            logger.info("new client %s from %s", self.extranonce1, peer)
        self.subscription = sub
        await self.write(sub.response_line(self.extranonce1))

    async def _handle_configure(self, conf: messages.Configure) -> None:
        await self.write(conf.response_line())

    async def _handle_authorize(self, auth: messages.Authorize) -> None:
        self.authorization = auth
        if (
            self.suggested_difficulty is None
            and auth.starting_diff is not None
            and auth.starting_diff > self.session_difficulty
        ):
            self.session_difficulty = auth.starting_diff
        await self.write(auth.response_line())

    async def _handle_suggest_difficulty(self, suggest: messages.SuggestDifficulty) -> None:
        if self.used_suggested_difficulty:
            return
        self.suggested_difficulty = suggest.suggested_difficulty
        self.session_difficulty = suggest.suggested_difficulty
        await self.write(messages.set_difficulty_line(self.session_difficulty))
        self.used_suggested_difficulty = True

    # --- initialization ----------------------------------------------------

    async def _init_stratum(self) -> None:
        self.initialized = True

        if self.subscription.user_agent == "cpuminer":
            self.session_difficulty = 0.1

        if self.suggested_difficulty is None:
            if not await self.write(messages.set_difficulty_line(self.session_difficulty)):
                return

        self.ledger.open(
            self.extranonce1,
            address=self.authorization.address,
            worker=self.authorization.worker,
            user_agent=self.subscription.user_agent,
        )

        async def on_new_template(template: JobTemplate) -> None:
            if template.clear_jobs:
                self._submission_hashes.clear()
            await self._send_job(template)

        self._job_callback = on_new_template
        self.watcher.subscribe(self._job_callback)
        if self.watcher.current_template is not None:
            await self._send_job(self.watcher.current_template)

        self._background.append(asyncio.create_task(self._difficulty_loop()))

    def _payouts(self) -> list[tuple[str, float]]:
        dev_fee = self.settings.dev_fee_address
        no_fee = 0 != self.statistics.hash_rate < FIFTY_TH
        if no_fee or not dev_fee:
            return [(self.authorization.address, 100)]
        return [(dev_fee, 1.5), (self.authorization.address, 98.5)]

    async def _send_job(self, template: JobTemplate) -> None:
        job = MiningJob(
            job_id=self.jobs.next_job_id(),
            payouts=self._payouts(),
            template=template,
            pool_identifier=self.settings.pool_identifier,
            network=self.settings.network,
        )
        self.jobs.add_job(job)
        await self.write(job.notify_payload(template))

    async def _difficulty_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(DIFFICULTY_CHECK_INTERVAL)
            target = self.statistics.suggested_difficulty(self.session_difficulty)
            if target is None or target == self.session_difficulty:
                continue
            self.session_difficulty = target
            await self.write(messages.set_difficulty_line(target))
            template = self.watcher.current_template
            if template is None:
                continue
            # re-send work flagged clean so the new difficulty takes effect,
            # with a strictly advancing timestamp
            refreshed = JobTemplate(
                **{
                    **template.__dict__,
                    "timestamp": max(template.timestamp, int(time.time())),
                    "clear_jobs": True,
                }
            )
            await self._send_job(refreshed)

    # --- share handling ----------------------------------------------------

    async def _handle_submit(self, submission: messages.Submit) -> None:
        job = self.jobs.get_job(submission.job_id)
        if job is None:
            await self.write(
                messages.error_line(submission.id, messages.JOB_NOT_FOUND, "Job not found")
            )
            self.ledger.record_rejected(self.extranonce1)
            return
        template = self.jobs.get_template(job.job_template_id)
        if template is None:
            await self.write(
                messages.error_line(submission.id, messages.JOB_NOT_FOUND, "Job Template not found")
            )
            self.ledger.record_rejected(self.extranonce1)
            return

        dedupe_key = ":".join(
            [
                submission.job_id,
                submission.extranonce2,
                submission.ntime,
                submission.nonce,
                submission.version_mask,
            ]
        )
        if dedupe_key in self._submission_hashes:
            await self.write(
                messages.error_line(submission.id, messages.DUPLICATE_SHARE, "Duplicate share")
            )
            self.ledger.record_rejected(self.extranonce1)
            return
        self._submission_hashes.add(dedupe_key)

        version_mask = int(submission.version_mask, 16)
        nonce = int(submission.nonce, 16)
        timestamp = int(submission.ntime, 16)

        header = job.build_header(
            template, version_mask, nonce, self.extranonce1, submission.extranonce2, timestamp
        )
        difficulty = share_difficulty(hash256(header))

        if difficulty < self.session_difficulty:
            await self.write(
                messages.error_line(
                    submission.id, messages.LOW_DIFFICULTY_SHARE, "Difficulty too low"
                )
            )
            self.ledger.record_rejected(self.extranonce1)
            return

        await self.write(submission.response_line())
        self.statistics.add_share(self.session_difficulty)
        self.ledger.record_accepted(self.extranonce1, self.session_difficulty)

        if difficulty >= template.network_difficulty:
            logger.warning("!!! BLOCK FOUND !!! height=%s difficulty=%s", template.height, difficulty)
            block_hex = job.build_block_hex(
                template, version_mask, nonce, self.extranonce1, submission.extranonce2, timestamp
            )
            result = await self.rpc.submit_block(block_hex)
            self.ledger.record_block(self.extranonce1)
            if self.on_block_found:
                await self.on_block_found(
                    height=template.height,
                    miner_address=self.authorization.address,
                    worker=self.authorization.worker,
                    extranonce1=self.extranonce1,
                    block_hex=block_hex,
                    rpc_result=result,
                )
