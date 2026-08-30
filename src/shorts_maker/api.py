"""내부 API (문서 §10 C).

**Spring 백엔드만 호출한다.** 관리자 인증은 Spring 이 이미 하고 있으므로(REALM_ADMIN)
여기서 JWT 를 다시 검증하지 않는다 — 대신 이 서비스를 외부에 노출하지 않는 것이 전제다.

🔴 그 전제를 코드가 강제한다: 루프백이 아닌 주소에 바인딩하면서 SHORTS_API_TOKEN 이
없으면 **기동을 거부한다.** 설정 실수로 인증 없는 인스턴스가 외부에 열리는 경로를 막는다.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import (
    config, cutting, download, ffmpeg, ingest, jobs, media, pipeline, pricing, ranking,
    render, segmentation, stt, web,
)
from .db import store

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

cfg = config.load()
queue = jobs.JobQueue()
app = FastAPI(title="shorts_maker", docs_url="/docs")


def require_token(x_shorts_token: str | None = Header(default=None)) -> None:
    if cfg.api_token and x_shorts_token != cfg.api_token:
        raise HTTPException(status_code=401, detail="invalid X-Shorts-Token")


def connect() -> sqlite3.Connection:
    return store.connect(cfg.db_path)


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------- 조회

@app.get("/health")
def health() -> dict:
    with connect() as conn:
        version = store.schema_version(conn)
    active = queue.active()
    return {
        "ok": True,
        "schema": version,
        # 토큰 유무와 섞지 않는다 — 화면이 "Gemini 설정됨"으로 잘못 읽는다.
        "geminiKey": bool(cfg.gemini_api_key),
        "geminiModel": cfg.gemini_model,
        "whisperModel": cfg.whisper_model,
        "activeJob": active.as_dict() if active else None,
    }


@app.get("/api/sources", dependencies=[Depends(require_token)])
def list_sources() -> list[dict]:
    with connect() as conn:
        return rows(conn, "select * from sources order by id desc")


@app.get("/api/sources/{source_id}", dependencies=[Depends(require_token)])
def get_source(source_id: int) -> dict:
    with connect() as conn:
        found = conn.execute("select * from sources where id = ?", (source_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "source not found")
        chunks = rows(conn, "select * from chunks where source_id = ? order by idx", (source_id,))
        for chunk in chunks:
            chunk["utteranceCount"] = conn.execute(
                "select count(*) as n from utterances where chunk_id = ?", (chunk["id"],)
            ).fetchone()["n"]
            chunk["segments"] = rows(
                conn, "select * from segments where chunk_id = ? order by idx", (chunk["id"],)
            )
        runs = rows(conn, "select * from runs where source_id = ? order by id desc", (source_id,))
        for run in runs:
            if run.get("ranked"):
                try:
                    run["ranked"] = json.loads(run["ranked"])
                except json.JSONDecodeError:
                    pass
        clips = rows(
            conn,
            """select cl.*, sg.description from clips cl
               join segments sg on sg.id = cl.segment_id
               join chunks ch on ch.id = sg.chunk_id
               where ch.source_id = ? order by cl.id desc""",
            (source_id,),
        )
        for clip in clips:
            clip["reviews"] = rows(
                conn,
                "select * from clip_reviews where clip_id = ? order by id desc",
                (clip["id"],),
            )
        calls = rows(
            conn, "select * from stage_calls where source_id = ? order by id desc", (source_id,)
        )
        return {
            "source": dict(found),
            "chunks": chunks,
            "runs": runs,
            "clips": clips,
            "cost": pricing.estimate(calls, cfg),
            "stageCalls": calls[:50],
        }


@app.get("/api/chunks/{chunk_id}/utterances", dependencies=[Depends(require_token)])
def get_utterances(chunk_id: int) -> list[dict]:
    with connect() as conn:
        return rows(
            conn,
            "select idx, start_sec, end_sec, text, avg_logprob from utterances"
            " where chunk_id = ? order by idx",
            (chunk_id,),
        )


# ---------------------------------------------------------------- 잡

class SourceIn(BaseModel):
    path: str
    title: str
    contentType: str = "LECTURE"
    origin: str | None = None
    context: str | None = None
    adminId: int | None = None


class UrlIn(BaseModel):
    url: str
    context: str | None = None
    adminId: int | None = None


class RegisterIn(BaseModel):
    title: str | None = None
    origin: str | None = None
    context: str | None = None
    adminId: int | None = None


class SttIn(BaseModel):
    model: str | None = None
    initialPrompt: str | None = None
    force: bool = False


class ChunkIn(BaseModel):
    startSec: float
    endSec: float


class RankIn(BaseModel):
    criteria: str | None = None
    adminId: int | None = None


class ReviewIn(BaseModel):
    verdict: str
    note: str | None = None
    adminId: int | None = None


def submit(kind: str, target: str, fn) -> dict:
    return queue.submit(kind, target, fn).as_dict()


@app.post("/api/sources", dependencies=[Depends(require_token)])
def add_source(body: SourceIn) -> dict:
    with connect() as conn:
        try:
            source_id = ingest.add_source(
                conn, cfg, body.path, body.title, body.contentType, body.origin, body.context
            )
        except ingest.IngestError as exc:
            raise HTTPException(400, str(exc)) from exc
        if body.adminId is not None:
            conn.execute(
                "update sources set created_by_admin_id = ? where id = ?", (body.adminId, source_id)
            )
            conn.commit()
    return {"sourceId": source_id}


@app.get("/api/media", dependencies=[Depends(require_token)])
def list_media() -> dict:
    with connect() as conn:
        return {"items": media.listing(conn, cfg), "disk": media.disk_usage(cfg)}


@app.delete("/api/media/{name}", dependencies=[Depends(require_token)])
def delete_media(name: str) -> dict:
    """🔴 파일과 파생물을 실제로 지운다. 되돌릴 수 없다."""
    with connect() as conn:
        try:
            result = media.delete(conn, cfg, name)
        except media.MediaError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"removed": result.files, "freedBytes": result.freed_bytes, "sourceId": result.source_id}


@app.post("/api/media/{name}/register", dependencies=[Depends(require_token)])
def register_media(name: str, body: RegisterIn) -> dict:
    """이미 서버에 있는 파일을 원본으로 등록한다(scp 로 올려둔 경우)."""
    try:
        path = media.resolve(cfg, name)
    except media.MediaError as exc:
        raise HTTPException(400, str(exc)) from exc

    def work() -> dict:
        with connect() as conn:
            source_id = ingest.add_source(
                conn, cfg, path.name, body.title or path.stem, "LECTURE", body.origin, body.context
            )
            if body.adminId is not None:
                conn.execute(
                    "update sources set created_by_admin_id = ? where id = ?", (body.adminId, source_id)
                )
                conn.commit()
        return {"sourceId": source_id}

    return submit("register", name, work)


@app.post("/api/sources/from-url", dependencies=[Depends(require_token)])
def add_source_from_url(body: UrlIn) -> dict:
    """URL 로 받아서 바로 원본으로 등록한다.

    🔴 호스트는 유튜브로 제한된다(download.check_url) — 임의 URL 을 서버가 받게 하면
    내부망을 찌를 수 있다.
    """
    try:
        download.check_url(body.url)
    except download.DownloadError as exc:
        # 잡을 띄우기 전에 막는다. 큐에 넣고 실패하면 사용자는 한참 뒤에야 이유를 안다.
        raise HTTPException(400, str(exc)) from exc

    def work() -> dict:
        path, info = download.fetch(cfg, body.url)
        with connect() as conn:
            existing = media.find_source_id(conn, path.resolve())
            if existing is not None:
                return {"sourceId": existing, "reused": True, "title": info.title}
            source_id = ingest.add_source(
                conn, cfg, path.name, info.title, "LECTURE",
                f"{info.url} ({info.uploader})", body.context,
            )
            if body.adminId is not None:
                conn.execute(
                    "update sources set created_by_admin_id = ? where id = ?", (body.adminId, source_id)
                )
                conn.commit()
        return {"sourceId": source_id, "reused": False, "title": info.title}

    return submit("download", body.url, work)


@app.post("/api/sources/{source_id}/chunks", dependencies=[Depends(require_token)])
def add_chunk(source_id: int, body: ChunkIn) -> dict:
    def work() -> dict:
        with connect() as conn:
            chunk_id = ingest.add_chunk(conn, cfg, source_id, body.startSec, body.endSec)
        return {"chunkId": chunk_id}

    return submit("chunk", f"source {source_id}", work)


@app.post("/api/chunks/{chunk_id}/stt", dependencies=[Depends(require_token)])
def run_stt(chunk_id: int, body: SttIn) -> dict:
    def work() -> dict:
        with connect() as conn:
            result = stt.run_for_chunk(conn, cfg, chunk_id, body.model, body.force, body.initialPrompt)
        return {"utterances": len(result.rows), "transcribeMs": result.transcribe_ms, "model": result.model}

    return submit("stt", f"chunk {chunk_id}", work)


@app.post("/api/chunks/{chunk_id}/segment", dependencies=[Depends(require_token)])
def run_segment(chunk_id: int, force: bool = False) -> dict:
    def work() -> dict:
        with connect() as conn:
            specs = segmentation.run_for_chunk(conn, cfg, chunk_id, force)
        return {"segments": len(specs)}

    return submit("segment", f"chunk {chunk_id}", work)


@app.post("/api/sources/{source_id}/rank", dependencies=[Depends(require_token)])
def run_rank(source_id: int, body: RankIn) -> dict:
    def work() -> dict:
        with connect() as conn:
            run_id = ranking.run_for_source(conn, cfg, source_id, body.criteria, body.adminId)
        return {"runId": run_id}

    return submit("rank", f"source {source_id}", work)


@app.post("/api/runs/{run_id}/segments/{segment_id}/cut", dependencies=[Depends(require_token)])
def run_cut(
    run_id: int, segment_id: int, score: float | None = None, replace: bool = False
) -> dict:
    # 이미 있는지는 잡을 띄우기 전에 본다 — 큐에 넣고 나서 실패하면 사용자는 한참 기다린 뒤에야 안다.
    with connect() as conn:
        if not replace and cutting.existing_clip(conn, run_id, segment_id) is not None:
            raise HTTPException(409, "이 구간으로 만든 클립이 이미 있습니다")

    def work() -> dict:
        with connect() as conn:
            clip_id = cutting.run_for_segment(conn, cfg, run_id, segment_id, score, None, replace)
        return {"clipId": clip_id}

    return submit("cut", f"segment {segment_id}", work)


@app.post("/api/clips/{clip_id}/render", dependencies=[Depends(require_token)])
def run_render(clip_id: int, force: bool = False, subtitles: bool = True) -> dict:
    def work() -> dict:
        with connect() as conn:
            out = render.run_for_clip(conn, cfg, clip_id, force, subtitles)
        return {"path": str(out)}

    return submit("render", f"clip {clip_id}", work)


@app.post("/api/sources/{source_id}/pipeline", dependencies=[Depends(require_token)])
def run_pipeline(source_id: int, body: RankIn, resegment: bool = False) -> dict:
    """[3]→[5]→[6]→[7] 을 한 잡으로. 구간이 이미 있으면 다시 나누지 않는다(§3)."""

    def work() -> dict:
        with connect() as conn:
            return pipeline.run_all(conn, cfg, source_id, body.criteria, body.adminId, resegment)

    return submit("pipeline", f"source {source_id}", work)


@app.get("/api/cost", dependencies=[Depends(require_token)])
def total_cost() -> dict:
    with connect() as conn:
        return pricing.estimate(rows(conn, "select * from stage_calls"), cfg)


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> dict:
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job.as_dict()


@app.get("/api/jobs", dependencies=[Depends(require_token)])
def list_jobs() -> list[dict]:
    return [j.as_dict() for j in queue.recent()]


# ---------------------------------------------------------------- 결과물

@app.get("/api/clips/{clip_id}/file", dependencies=[Depends(require_token)])
def get_clip_file(clip_id: int) -> Any:
    with connect() as conn:
        clip = conn.execute("select path, rendered from clips where id = ?", (clip_id,)).fetchone()
    if clip is None or not clip["rendered"] or not clip["path"]:
        raise HTTPException(404, "clip not rendered")
    path = Path(clip["path"])
    if not path.is_file():
        raise HTTPException(404, "clip file missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/segments/{segment_id}/preview", dependencies=[Depends(require_token)])
def get_segment_preview(segment_id: int) -> Any:
    """구간을 원본 화면비 그대로 잘라 보여준다.

    클립을 만들기 전에 "이 구간이 볼 만한가"를 눈으로 확인하는 용도다. 재인코딩 없이
    떠내므로 몇 초면 만들어지고, 한 번 만들면 캐시된다.
    """
    with connect() as conn:
        segment = conn.execute(
            """select sg.*, s.path as source_path from segments sg
               join chunks ch on ch.id = sg.chunk_id
               join sources s on s.id = ch.source_id
               where sg.id = ?""",
            (segment_id,),
        ).fetchone()
    if segment is None:
        raise HTTPException(404, "segment not found")

    out_dir = cfg.work_dir / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"segment{segment_id:04d}.mp4"
    if not out.is_file():
        try:
            ffmpeg.copy_segment(
                segment["source_path"], str(out),
                float(segment["start_sec"]), float(segment["end_sec"]), cfg.ffmpeg_bin,
            )
        except ffmpeg.FfmpegError as exc:
            raise HTTPException(500, f"미리보기 생성 실패: {exc}") from exc
    return FileResponse(out, media_type="video/mp4", filename=out.name)


@app.post("/api/clips/{clip_id}/review", dependencies=[Depends(require_token)])
def add_review(clip_id: int, body: ReviewIn) -> dict:
    if body.verdict not in ("OK", "NG"):
        raise HTTPException(400, "verdict must be OK or NG")
    with connect() as conn:
        if conn.execute("select 1 from clips where id = ?", (clip_id,)).fetchone() is None:
            raise HTTPException(404, "clip not found")
        conn.execute(
            "insert into clip_reviews (clip_id, admin_id, verdict, note) values (?, ?, ?, ?)",
            (clip_id, body.adminId, body.verdict, body.note),
        )
        conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 개발용 화면

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """토큰 없이 여는 로컬 확인용 화면. 운영 화면은 관리자 페이지(admin-web)다."""
    return web.INDEX_HTML


def check_binding() -> None:
    if cfg.api_host not in LOOPBACK and not cfg.api_token:
        raise SystemExit(
            f"거부: {cfg.api_host} 에 바인딩하려면 SHORTS_API_TOKEN 이 필요하다.\n"
            "이 서비스는 관리자 인증을 Spring 에 의존한다 — 토큰 없이 외부에 열면 무인증으로 노출된다."
        )


def serve() -> None:
    import uvicorn

    check_binding()
    with connect() as conn:
        store.apply_schema(conn)
        # 재기동 시 매달린 잡 정리 — 워커는 메모리에만 있으므로 RUNNING 은 이미 죽은 것이다.
        conn.execute("update sources set status = 'FAILED', error = '재기동으로 중단됨' where status = 'RUNNING'")
        conn.execute("update runs set status = 'FAILED', error = '재기동으로 중단됨' where status = 'RUNNING'")
        conn.commit()
    uvicorn.run(app, host=cfg.api_host, port=cfg.api_port, log_level="info")
