"""Stack actions (up, down, restart) run as background jobs.

`up --pull` can take minutes on a Pi, longer than a request should wait. A request queues a
job and gets its ID back; `GET /api/v1/jobs/{id}` reports progress and Compose's output.

Jobs run one at a time, in the order queued, so two Compose operations never race on the
same project. They live in memory: a restart of the API forgets them (the audit log keeps
who ran what).
"""

from __future__ import annotations

import collections
import contextvars
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from p4n4_lib import compose

from p4n4_api import audit

ACTIONS = ("up", "down", "restart")
KEEP_JOBS = 100  # finished jobs beyond this are forgotten, oldest first
OUTPUT_LINES = 500  # per job, the most recent
TIMEOUT_S = 30 * 60


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    action: str
    stack: str  # a stack name, or "all"
    service: str | None
    pull: bool
    requested_by: str
    # (stack, directory) pairs, in the order they're acted on
    targets: list[tuple[str, Path]]
    status: str = "queued"  # queued, running, succeeded, failed
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    output: collections.deque[str] = field(
        default_factory=lambda: collections.deque(maxlen=OUTPUT_LINES)
    )

    def log(self, line: str) -> None:
        with _lock:  # requests read the output while the job writes it
            self.output.append(line)

    @property
    def target(self) -> str:
        return f"{self.stack}/{self.service}" if self.service else self.stack

    def snapshot(self) -> dict:
        with _lock:
            return self._snapshot()

    def _snapshot(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "stack": self.stack,
            "service": self.service,
            "pull": self.pull,
            "requested_by": self.requested_by,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "output": list(self.output),
        }


_jobs: dict[str, Job] = {}  # insertion order = creation order
# Reentrant: get() holds it while taking a job's snapshot, which takes it too.
_lock = threading.RLock()
_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="p4n4-job")
        return _executor


def shutdown() -> None:
    """Stop taking jobs; queued ones are dropped (a running Compose process finishes)."""
    global _executor
    with _lock:
        pool, _executor = _executor, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def reset() -> None:
    """For tests: forget every job."""
    shutdown()
    with _lock:
        _jobs.clear()


def _commands(action: str, service: str | None, pull: bool) -> list[list[str]]:
    """Compose argument lists for one stack. Never `down -v`: that deletes data."""
    if action == "up":
        if pull and compose.compose_cmd() == ("docker-compose",):
            # docker-compose v1 has no `up --pull`; pull explicitly first
            return [["pull"], ["up", "-d"]]
        return [["up", "-d", *(["--pull=always"] if pull else [])]]
    if action == "down":
        return [["down"]]
    return [["restart", *([service] if service else [])]]


def _run(job: Job, cwd: Path, args: list[str], deadline: float) -> int:
    cmd = [*compose.compose_cmd(), *args]
    job.log(f"$ {' '.join(cmd)}  # in {cwd}")
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
    )
    # A watchdog rather than a read timeout: Compose can be silent for minutes while pulling.
    killed = threading.Event()

    def kill() -> None:
        killed.set()
        proc.kill()

    timer = threading.Timer(max(0.0, deadline - time.monotonic()), kill)
    timer.start()
    try:
        for line in proc.stdout:
            job.log(line.rstrip("\n")[:2000])
        code = proc.wait()
    finally:
        timer.cancel()
    if killed.is_set():
        job.log(f"Timed out after {TIMEOUT_S} s; stopped.")
        return code or 1
    return code


def _execute(job: Job) -> None:
    job.status, job.started_at = "running", _now()
    deadline = time.monotonic() + TIMEOUT_S
    code = 0
    try:
        for name, cwd in job.targets:
            if len(job.targets) > 1:
                job.log(f"── {name} ──")
            for args in _commands(job.action, job.service, job.pull):
                code = _run(job, cwd, args, deadline)
                if code != 0:
                    break
            if code != 0:
                break  # later stacks may depend on this one
    except Exception as exc:  # e.g. Compose missing: report it on the job, not just the log
        job.log(f"Error: {exc}")
        code = 1
    job.exit_code = code
    job.status = "succeeded" if code == 0 else "failed"
    job.finished_at = _now()
    audit.record(job.requested_by, f"stack.{job.action}", job.target, f"{job.status} ({code})")


def submit(
    action: str,
    stack: str,
    targets: list[tuple[str, Path]],
    requested_by: str,
    service: str | None = None,
    pull: bool = False,
) -> tuple[Job, bool]:
    """Queue a job. Returns (job, created); an identical job still waiting is reused instead,
    so a double-clicked button doesn't run Compose twice."""
    if action == "down":
        targets = list(reversed(targets))  # dependents first
    pool = _pool()
    with _lock:
        for job in _jobs.values():
            same = (job.action, job.stack, job.service, job.pull) == (action, stack, service, pull)
            if same and job.status == "queued":
                return job, False
        job = Job(
            id=uuid.uuid4().hex,
            action=action,
            stack=stack,
            service=service,
            pull=pull,
            requested_by=requested_by,
            targets=targets,
        )
        _jobs[job.id] = job
        finished = [j.id for j in _jobs.values() if j.finished_at]
        for old in finished[: max(0, len(_jobs) - KEEP_JOBS)]:
            del _jobs[old]
    # Run with the submitting request's context, so the job's log lines carry its request ID.
    pool.submit(contextvars.copy_context().run, _execute, job)
    return job, True


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return job.snapshot() if job else None


def recent(limit: int) -> list[dict]:
    """Newest first, without output."""
    with _lock:
        jobs = list(_jobs.values())[-limit:]
    return [{**j.snapshot(), "output": []} for j in reversed(jobs)]
