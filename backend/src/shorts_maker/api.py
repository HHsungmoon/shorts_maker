"""HTTP API 와 프론트 서빙 (문서 §10 C · §13).

원래는 Spring 백엔드만 호출하는 내부 API 였다 — 관리자 인증은 backend 가 이미 했고
이 서비스는 도커 네트워크 안에서만 닿았다. **독립 서비스가 되면서 그 전제가 사라졌다.**
이제 브라우저가 직접 붙으므로 여기서 인증한다(auth.py — 비밀번호 1개 + 세션 쿠키).

🔴 그 전제를 코드가 강제한다: 루프백이 아닌 주소에 바인딩하면서 SHORTS_ADMIN_PASSWORD
가 없으면 **기동을 거부한다**(check_binding). 설정 실수로 무인증 인스턴스가 외부에
열리는 경로를 막는다.
"""

import hmac
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import (
    auth, config, cutting, download, ffmpeg, ingest, jobs, media, pipeline, pricing, ranking,
    render, segmentation, stt, web,
)
from .config import LOOPBACK
from .db import store

cfg = config.load()
queue = jobs.JobQueue()
app = FastAPI(title="shorts_maker", docs_url="/docs")


def require_auth(request: Request, x_shorts_token: str | None = Header(default=None)) -> None:
    """세션 쿠키 **또는** X-Shorts-Token 중 하나면 통과한다.

    쿠키는 브라우저용, 토큰은 CLI·스크립트 같은 기계 클라이언트용이다. 둘 다 설정돼
    있지 않으면 인증하지 않는다 — 루프백 전용 로컬 개발 모드이고, 그 상태로 외부에
    열리는 것은 check_binding 이 막는다.
    """
    if not cfg.admin_password and not cfg.api_token:
        return
    if cfg.api_token and x_shorts_token and hmac.compare_digest(x_shorts_token, cfg.api_token):
        return
    if cfg.admin_password:
        token = request.cookies.get(auth.COOKIE_NAME)
        if token and auth.verify(cfg.admin_password, token):
            return
    raise HTTPException(status_code=401, detail="로그인이 필요합니다")


def connect() -> sqlite3.Connection:
    return store.connect(cfg.db_path)


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------- 로그인

class LoginIn(BaseModel):
    password: str


@app.post("/auth/login")
def login(body: LoginIn, response: Response) -> dict:
    if not cfg.admin_password:
        # 로컬 개발 모드. 로그인 화면을 띄울 이유가 없으므로 프론트가 이 응답을 보고 건너뛴다.
        return {"ok": True, "authRequired": False}
    locked = auth.locked_for()
    if locked:
        raise HTTPException(429, f"시도가 너무 많습니다. {locked}초 뒤에 다시 시도하세요.")
    if not auth.check(cfg.admin_password, body.password):
        raise HTTPException(401, "비밀번호가 맞지 않습니다")
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.issue(cfg.admin_password, cfg.session_ttl_hours),
        max_age=cfg.session_ttl_hours * 3600,
        # 🔴 httponly: 쿠키를 JS 에서 못 읽게 한다. XSS 가 나도 세션이 바로 새지는 않는다
        # (admin-web 은 localStorage 였고, 그건 스크립트가 그대로 읽어간다).
        httponly=True,
        # lax: 외부 사이트에서 넘어온 POST 에 쿠키가 실리지 않는다 → CSRF 의 기본 경로가 막힌다.
        samesite="lax",
        secure=cfg.cookie_secure,
        path="/",
    )
    return {"ok": True, "authRequired": True}


@app.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/auth/me")
def me(request: Request) -> dict:
    """프론트가 로그인 화면을 띄울지 결정하는 용도. 401 을 쓰지 않는다 —
    "로그인 안 됨"은 정상 상태이고, 에러로 만들면 콘솔이 매번 빨개진다."""
    if not cfg.admin_password:
        return {"authenticated": True, "authRequired": False}
    token = request.cookies.get(auth.COOKIE_NAME)
    return {
        "authenticated": bool(token and auth.verify(cfg.admin_password, token)),
        "authRequired": True,
    }


# ---------------------------------------------------------------- 조회

@app.get("/health")
def health() -> dict:
    """무인증. 도커 헬스체크와 nginx 가 부른다.

    🔴 설정값을 여기 담지 않는다. backend 프록시 뒤에 있을 때는 이 응답이 도커 네트워크
    밖으로 안 나갔지만 지금은 인터넷에 열려 있다 — 모델명·스키마 버전은 공격자에게
    먼저 주지 않는다. 화면이 쓰는 값은 /api/status 로 옮겼다.
    """
    return {"ok": True}


@app.get("/api/status", dependencies=[Depends(require_auth)])
def status() -> dict:
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
        "languages": stt.LANGUAGES,
        "activeJob": active.as_dict() if active else None,
    }


@app.get("/api/sources", dependencies=[Depends(require_auth)])
def list_sources() -> list[dict]:
    with connect() as conn:
        return rows(conn, "select * from sources order by id desc")


@app.get("/api/sources/{source_id}", dependencies=[Depends(require_auth)])
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


@app.get("/api/chunks/{chunk_id}/utterances", dependencies=[Depends(require_auth)])
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


class UrlIn(BaseModel):
    url: str
    context: str | None = None
    language: str | None = None


class RegisterIn(BaseModel):
    title: str | None = None
    origin: str | None = None
    context: str | None = None
    language: str | None = None


class SttIn(BaseModel):
    model: str | None = None
    initialPrompt: str | None = None
    language: str | None = None
    force: bool = False


class ChunkIn(BaseModel):
    startSec: float
    endSec: float


class RankIn(BaseModel):
    criteria: str | None = None


class ReviewIn(BaseModel):
    verdict: str
    note: str | None = None


def submit(kind: str, target: str, fn) -> dict:
    return queue.submit(kind, target, fn).as_dict()


@app.post("/api/sources", dependencies=[Depends(require_auth)])
def add_source(body: SourceIn) -> dict:
    with connect() as conn:
        try:
            source_id = ingest.add_source(
                conn, cfg, body.path, body.title, body.contentType, body.origin, body.context
            )
        except ingest.IngestError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"sourceId": source_id}


@app.get("/api/media", dependencies=[Depends(require_auth)])
def list_media() -> dict:
    with connect() as conn:
        return {"items": media.listing(conn, cfg), "disk": media.disk_usage(cfg)}


@app.delete("/api/media/{name}", dependencies=[Depends(require_auth)])
def delete_media(name: str) -> dict:
    """🔴 파일과 파생물을 실제로 지운다. 되돌릴 수 없다."""
    with connect() as conn:
        try:
            result = media.delete(conn, cfg, name)
        except media.MediaError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"removed": result.files, "freedBytes": result.freed_bytes, "sourceId": result.source_id}


@app.post("/api/media/{name}/register", dependencies=[Depends(require_auth)])
def register_media(name: str, body: RegisterIn) -> dict:
    """이미 서버에 있는 파일을 원본으로 등록한다(scp 로 올려둔 경우)."""
    try:
        path = media.resolve(cfg, name)
    except media.MediaError as exc:
        raise HTTPException(400, str(exc)) from exc

    def work() -> dict:
        with connect() as conn:
            source_id = ingest.add_source(
                conn, cfg, path.name, body.title or path.stem, "LECTURE", body.origin,
                body.context, stt.check_language(body.language),
            )
        return {"sourceId": source_id}

    return submit("register", name, work)


@app.post("/api/sources/from-url", dependencies=[Depends(require_auth)])
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
                f"{info.url} ({info.uploader})", body.context, stt.check_language(body.language),
            )
        return {"sourceId": source_id, "reused": False, "title": info.title}

    return submit("download", body.url, work)


@app.post("/api/sources/{source_id}/chunks", dependencies=[Depends(require_auth)])
def add_chunk(source_id: int, body: ChunkIn) -> dict:
    def work() -> dict:
        with connect() as conn:
            chunk_id = ingest.add_chunk(conn, cfg, source_id, body.startSec, body.endSec)
        return {"chunkId": chunk_id}

    return submit("chunk", f"source {source_id}", work)


@app.post("/api/chunks/{chunk_id}/stt", dependencies=[Depends(require_auth)])
def run_stt(chunk_id: int, body: SttIn) -> dict:
    def work() -> dict:
        with connect() as conn:
            result = stt.run_for_chunk(
                conn, cfg, chunk_id, body.model, body.force, body.initialPrompt, body.language
            )
        return {"utterances": len(result.rows), "transcribeMs": result.transcribe_ms, "model": result.model}

    return submit("stt", f"chunk {chunk_id}", work)


@app.post("/api/chunks/{chunk_id}/segment", dependencies=[Depends(require_auth)])
def run_segment(chunk_id: int, force: bool = False) -> dict:
    def work() -> dict:
        with connect() as conn:
            specs = segmentation.run_for_chunk(conn, cfg, chunk_id, force)
        return {"segments": len(specs)}

    return submit("segment", f"chunk {chunk_id}", work)


@app.post("/api/sources/{source_id}/rank", dependencies=[Depends(require_auth)])
def run_rank(source_id: int, body: RankIn) -> dict:
    def work() -> dict:
        with connect() as conn:
            run_id = ranking.run_for_source(conn, cfg, source_id, body.criteria)
        return {"runId": run_id}

    return submit("rank", f"source {source_id}", work)


@app.post("/api/runs/{run_id}/segments/{segment_id}/cut", dependencies=[Depends(require_auth)])
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


@app.post("/api/clips/{clip_id}/render", dependencies=[Depends(require_auth)])
def run_render(clip_id: int, force: bool = False, subtitles: bool = True) -> dict:
    def work() -> dict:
        with connect() as conn:
            out = render.run_for_clip(conn, cfg, clip_id, force, subtitles)
        return {"path": str(out)}

    return submit("render", f"clip {clip_id}", work)


@app.post("/api/sources/{source_id}/pipeline", dependencies=[Depends(require_auth)])
def run_pipeline(source_id: int, body: RankIn, resegment: bool = False) -> dict:
    """[3]→[5]→[6]→[7] 을 한 잡으로. 구간이 이미 있으면 다시 나누지 않는다(§3)."""

    def work() -> dict:
        with connect() as conn:
            return pipeline.run_all(conn, cfg, source_id, body.criteria, resegment)

    return submit("pipeline", f"source {source_id}", work)


@app.get("/api/cost", dependencies=[Depends(require_auth)])
def total_cost() -> dict:
    with connect() as conn:
        return pricing.estimate(rows(conn, "select * from stage_calls"), cfg)


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def get_job(job_id: str) -> dict:
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job.as_dict()


@app.get("/api/jobs", dependencies=[Depends(require_auth)])
def list_jobs() -> list[dict]:
    return [j.as_dict() for j in queue.recent()]


# ---------------------------------------------------------------- 결과물

@app.get("/api/clips/{clip_id}/file", dependencies=[Depends(require_auth)])
def get_clip_file(clip_id: int) -> Any:
    with connect() as conn:
        clip = conn.execute("select path, rendered from clips where id = ?", (clip_id,)).fetchone()
    if clip is None or not clip["rendered"] or not clip["path"]:
        raise HTTPException(404, "clip not rendered")
    path = Path(clip["path"])
    if not path.is_file():
        raise HTTPException(404, "clip file missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/segments/{segment_id}/preview", dependencies=[Depends(require_auth)])
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


@app.post("/api/clips/{clip_id}/review", dependencies=[Depends(require_auth)])
def add_review(clip_id: int, body: ReviewIn) -> dict:
    if body.verdict not in ("OK", "NG"):
        raise HTTPException(400, "verdict must be OK or NG")
    with connect() as conn:
        if conn.execute("select 1 from clips where id = ?", (clip_id,)).fetchone() is None:
            raise HTTPException(404, "clip not found")
        conn.execute(
            "insert into clip_reviews (clip_id, verdict, note) values (?, ?, ?)",
            (clip_id, body.verdict, body.note),
        )
        conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 프론트

# 🔴 이 아래 라우트들은 경로를 통째로 삼키므로 **반드시 API 라우트 뒤에** 등록돼야 한다.
# FastAPI 는 등록 순서대로 매칭한다 — 위로 올리면 /api/** 가 전부 프론트로 빨려 들어간다.

@app.get("/debug", response_class=HTMLResponse)
def debug_page() -> str:
    """node 빌드 없이 파이프라인 상태를 보는 화면. `web/dist` 가 없을 때 `/` 도 여기로 온다."""
    return web.INDEX_HTML


def _spa_file(rel: str) -> Path | None:
    """web/dist 안의 실제 파일만 돌려준다. 없으면 None.

    🔴 `..` 로 디렉터리를 빠져나가는 요청을 막는다 — 정적 서빙에서 이 검사를 빼면
    서버의 아무 파일이나 읽힌다.
    """
    root = cfg.web_dir.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> Any:
    index = cfg.web_dir / "index.html"
    if not index.is_file():
        # 아직 프론트를 빌드하지 않았다. 루트만 개발용 화면으로 받아주고 나머지는 404 다.
        if full_path in ("", "index.html"):
            return HTMLResponse(web.INDEX_HTML)
        raise HTTPException(404, "not found")
    # 없는 API 경로가 index.html 로 200 을 받으면 프론트가 HTML 을 JSON 으로 파싱하다
    # 엉뚱한 곳에서 죽는다. 여기서 404 로 끊는다.
    if full_path.startswith(("api/", "auth/", "health")):
        raise HTTPException(404, "not found")
    found = _spa_file(full_path) if full_path else None
    # 클라이언트 라우팅(/login 등)은 파일이 없다 — index.html 을 주고 브라우저가 처리한다.
    return FileResponse(found or index)


def check_binding() -> None:
    if cfg.api_host not in LOOPBACK and not cfg.admin_password:
        raise SystemExit(
            f"거부: {cfg.api_host} 에 바인딩하려면 SHORTS_ADMIN_PASSWORD 가 필요하다.\n"
            "브라우저가 직접 붙는 서비스라 비밀번호 없이 외부에 열면 무인증으로 노출된다.\n"
            "  openssl rand -base64 24"
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
