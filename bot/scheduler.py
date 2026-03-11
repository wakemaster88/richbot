"""Async scheduler for recurring Telegram reports and tasks."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

JOBS_FILE = Path("data/cronjobs.json")


class CronJob:
    __slots__ = ("name", "schedule", "job_type", "message", "enabled")

    def __init__(self, name: str, schedule: str, job_type: str, message: str = "", enabled: bool = True):
        self.name = name
        self.schedule = schedule
        self.job_type = job_type
        self.message = message
        self.enabled = enabled

    @property
    def hour(self) -> int:
        return int(self.schedule.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.schedule.split(":")[1])

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "schedule": self.schedule,
            "type": self.job_type,
            "message": self.message,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CronJob:
        return cls(
            name=d["name"],
            schedule=d["schedule"],
            job_type=d.get("type", "custom"),
            message=d.get("message", ""),
            enabled=d.get("enabled", True),
        )


class Scheduler:
    """Simple async scheduler that checks every 30s and fires jobs at HH:MM."""

    def __init__(self):
        self._jobs: list[CronJob] = []
        self._handlers: dict[str, Callable[..., Coroutine]] = {}
        self._task: asyncio.Task | None = None
        self._running = False
        self._fired_today: set[str] = set()
        self._load_jobs()

    def _load_jobs(self):
        if JOBS_FILE.exists():
            try:
                data = json.loads(JOBS_FILE.read_text())
                self._jobs = [CronJob.from_dict(j) for j in data]
                logger.info("Loaded %d cronjobs from %s", len(self._jobs), JOBS_FILE)
            except Exception as e:
                logger.warning("Failed to load cronjobs: %s", e)

    def _save_jobs(self):
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        JOBS_FILE.write_text(json.dumps([j.to_dict() for j in self._jobs], indent=2))

    def register_handler(self, job_type: str, handler: Callable[..., Coroutine]):
        self._handlers[job_type] = handler

    def add_job(self, name: str, schedule: str, job_type: str, message: str = "") -> CronJob:
        for j in self._jobs:
            if j.name == name:
                j.schedule = schedule
                j.job_type = job_type
                j.message = message
                j.enabled = True
                self._save_jobs()
                logger.info("Updated cronjob: %s at %s", name, schedule)
                return j

        job = CronJob(name=name, schedule=schedule, job_type=job_type, message=message)
        self._jobs.append(job)
        self._save_jobs()
        logger.info("Added cronjob: %s at %s [%s]", name, schedule, job_type)
        return job

    def remove_job(self, name: str) -> bool:
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j.name != name]
        if len(self._jobs) < before:
            self._save_jobs()
            logger.info("Removed cronjob: %s", name)
            return True
        return False

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self._jobs]

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler started with %d jobs", len(self._jobs))

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    async def _loop(self):
        while self._running:
            try:
                now = datetime.now()
                today_key = now.strftime("%Y-%m-%d")

                if now.hour == 0 and now.minute == 0:
                    self._fired_today.clear()

                for job in self._jobs:
                    if not job.enabled:
                        continue
                    fire_key = f"{today_key}:{job.name}"
                    if fire_key in self._fired_today:
                        continue
                    if now.hour == job.hour and now.minute == job.minute:
                        self._fired_today.add(fire_key)
                        handler = self._handlers.get(job.job_type)
                        if handler:
                            logger.info("Firing cronjob: %s [%s]", job.name, job.job_type)
                            try:
                                await handler(job)
                            except Exception as e:
                                logger.error("Cronjob %s failed: %s", job.name, e)
                        else:
                            logger.warning("No handler for job type: %s", job.job_type)
            except Exception as e:
                logger.error("Scheduler loop error: %s", e)

            await asyncio.sleep(30)
