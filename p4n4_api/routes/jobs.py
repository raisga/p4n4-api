"""Background jobs (stack actions): progress and output."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from p4n4_api import jobs
from p4n4_api.routes.control import JobOut

router = APIRouter(prefix="/jobs", tags=["stack control"])


@router.get("")
def list_jobs(limit: Annotated[int, Query(ge=1, le=jobs.KEEP_JOBS)] = 20) -> list[JobOut]:
    """Recent jobs, newest first, without their output. Kept in memory: an API restart
    forgets them (the audit log keeps who ran what)."""
    return [JobOut(**job) for job in jobs.recent(limit)]


@router.get("/{job_id}")
def get_job(job_id: str) -> JobOut:
    """One job, with the most recent lines of Compose's output."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job '{job_id}'.")
    return JobOut(**job)
