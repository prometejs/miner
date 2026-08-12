"""Registries for live templates and jobs (ids, lookup, 5-minute expiry)."""

import time

from .mining_job import MiningJob
from .template import JobTemplate

_EXPIRY_SECONDS = 5 * 60


class JobsManager:
    def __init__(self) -> None:
        self.latest_job_id = 1
        self.latest_template_id = 1
        self.jobs: dict[str, MiningJob] = {}
        self.templates: dict[str, JobTemplate] = {}

    def next_template_id(self) -> str:
        return format(self.latest_template_id, "x")

    def next_job_id(self) -> str:
        job_id = format(self.latest_job_id, "x")
        self.latest_job_id += 1
        return job_id

    def register_template(self, template: JobTemplate) -> None:
        if template.clear_jobs:
            self.templates = {}
            self.jobs = {}
        else:
            now = time.time()
            self.templates = {
                tid: t for tid, t in self.templates.items() if now - t.creation <= _EXPIRY_SECONDS
            }
            self.jobs = {
                jid: j for jid, j in self.jobs.items() if now - j.creation <= _EXPIRY_SECONDS
            }
        self.templates[template.id] = template
        self.latest_template_id += 1

    def add_job(self, job: MiningJob) -> None:
        self.jobs[job.job_id] = job

    def get_job(self, job_id: str) -> MiningJob | None:
        return self.jobs.get(job_id)

    def get_template(self, template_id: str) -> JobTemplate | None:
        return self.templates.get(template_id)
