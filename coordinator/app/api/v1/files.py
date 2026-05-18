from __future__ import annotations

import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from ...config import settings
from ...core.dependencies import DbSession
from ...core.exceptions import http_job_not_found
from ...core.http_headers import content_disposition_attachment
from ...core.security import safe_join
from ...models.enums import JobStatus
from ...models.job import Job

router = APIRouter(prefix="/files", tags=["files"])

_CHUNK_SIZE = 1024 * 1024  # 1 MB streaming chunks


@router.get("/{job_id}/download", response_model=None)
async def download_job_file(
    job_id: uuid.UUID,
    db: DbSession,
    request: Request,
) -> StreamingResponse | FileResponse:
    """
    Stream the input MP3 file to the requesting worker.
    Supports HTTP Range requests for resumable downloads.
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise http_job_not_found(job_id)

    file_path = safe_join(settings.input_base_dir, job.input_path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Input file not found on coordinator: {job.input_path}",
        )

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        # Parse "bytes=start-end"
        try:
            range_val = range_header.replace("bytes=", "")
            start_str, _, end_str = range_val.partition("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
        except ValueError:
            raise HTTPException(status_code=416, detail="Invalid Range header")

        if start >= file_size or end >= file_size or start > end:
            raise HTTPException(status_code=416, detail="Range not satisfiable")

        length = end - start + 1
        return StreamingResponse(
            _stream_file(file_path, start, length),
            status_code=206,
            media_type="audio/mpeg",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(length),
                "Content-Disposition": content_disposition_attachment(file_path.name),
                "Accept-Ranges": "bytes",
            },
        )

    return StreamingResponse(
        _stream_file(file_path, 0, file_size),
        media_type="audio/mpeg",
        headers={
            "Content-Length": str(file_size),
            "Content-Disposition": content_disposition_attachment(file_path.name),
            "Accept-Ranges": "bytes",
        },
    )


async def _stream_file(path: Path, start: int, length: int):
    async with aiofiles.open(path, "rb") as f:
        await f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = await f.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


# ── Output file download (dashboard) ─────────────────────────────────────────


@router.get("/output/{job_id}/srt", response_model=None)
async def download_srt(job_id: uuid.UUID, db: DbSession) -> FileResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise http_job_not_found(job_id)
    if job.status != JobStatus.COMPLETED or not job.output_srt_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SRT output not available — job not completed",
        )
    file_path = safe_join(settings.output_base_dir, job.output_srt_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="SRT file missing from disk")
    return FileResponse(
        path=str(file_path),
        media_type="application/x-subrip",
        filename=file_path.name,
    )


@router.get("/output/{job_id}/json", response_model=None)
async def download_json(job_id: uuid.UUID, db: DbSession) -> FileResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise http_job_not_found(job_id)
    if job.status != JobStatus.COMPLETED or not job.output_json_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="JSON output not available — job not completed",
        )
    file_path = safe_join(settings.output_base_dir, job.output_json_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="JSON file missing from disk")
    return FileResponse(
        path=str(file_path),
        media_type="application/json",
        filename=file_path.name,
    )
