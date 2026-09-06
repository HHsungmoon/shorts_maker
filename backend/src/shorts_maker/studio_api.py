"""스튜디오 API — 크리에이터(운영자)가 쓰는 `/api/**` 전부. **인증 필요.**

🔴 인증은 라우터 레벨로 걸려 있다(`router.dependencies`). 여기 `@router.get/post` 로 등록하는
라우트는 무엇이든 `require_auth` 를 거친다 — 엔드포인트마다 `Depends` 를 반복하지 않고,
그래서 빠뜨릴 수도 없다. `tests/test_api_auth.py` 가 이 라우터의 모든 라우트를 순회하며
무인증 401 을 확인한다.

시청자용 공개 API(`/api/watch/**`, tease §7-1) 는 M3 에서 **별도 라우터**(watch_api.py) 로
붙는다. 여기 넣으면 인증이 걸려 시청자가 못 쓰고, 저기 넣을 걸 여기 넣는 실수는 위 테스트가
잡지 못한다 — 공개 라우터에는 발행된 것만 읽는 조건이 따로 있다.

`deps.cfg` 는 **속성으로** 읽는다(`deps.cfg.xxx`). 값을 import 해 오면 테스트의 교체가
반영되지 않는다(deps.py 주석).
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import (
    cutting, deps, download, ffmpeg, ingest, media, pipeline, pricing, ranking, render,
    segmentation, stt,
)
from .db import store
from .deps import connect, require_auth, rows, submit

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


# ---------------------------------------------------------------- 조회

@router.get("/status")
def status() -> dict:
    with connect() as conn:
        version = store.schema_version(conn)
    active = deps.queue.active()
    return {
        "ok": True,
        "schema": version,
        # 토큰 유무와 섞지 않는다 — 화면이 "Gemini 설정됨"으로 잘못 읽는다.
        "geminiKey": bool(deps.cfg.gemini_api_key),
        "geminiModel": deps.cfg.gemini_model,
        "whisperModel": deps.cfg.whisper_model,
        "languages": stt.LANGUAGES,
        "activeJob": active.as_dict() if active else None,
    }


@router.get("/sources")
def list_sources() -> list[dict]:
    with connect() as conn:
        return rows(conn, "select * from sources order by id desc")


@router.get("/sources/{source_id}")
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
            "cost": pricing.estimate(calls, deps.cfg),
            "stageCalls": calls[:50],
        }


@router.get("/chunks/{chunk_id}/utterances")
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
    # 🔴 기존 청크와 그 아래 전부(발화·구간·클립·run)를 지우고 다시 만든다. 화면의 "다시 추출".
    replace: bool = False


class RankIn(BaseModel):
    criteria: str | None = None


class ReviewIn(BaseModel):
    verdict: str
    note: str | None = None


def _language(code: str | None) -> str | None:
    """잡을 띄우기 전에 언어 코드를 검사한다 — 큐에 넣고 나서 실패하면 늦게 안다."""
    try:
        return stt.check_language(code)
    except stt.SttError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sources")
def add_source(body: SourceIn) -> dict:
    with connect() as conn:
        try:
            source_id = ingest.add_source(
                conn, deps.cfg, body.path, body.title, body.contentType, body.origin, body.context
            )
        except ingest.IngestError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"sourceId": source_id}


@router.get("/media")
def list_media() -> dict:
    with connect() as conn:
        return {"items": media.listing(conn, deps.cfg), "disk": media.disk_usage(deps.cfg)}


@router.delete("/media/{name}")
def delete_media(name: str) -> dict:
    """🔴 파일과 파생물을 실제로 지운다. 되돌릴 수 없다."""
    with connect() as conn:
        try:
            result = media.delete(conn, deps.cfg, name)
        except media.MediaError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"removed": result.files, "freedBytes": result.freed_bytes, "sourceId": result.source_id}


@router.post("/media/{name}/register")
def register_media(name: str, body: RegisterIn) -> dict:
    """이미 서버에 있는 파일을 원본으로 등록한다(scp 로 올려둔 경우).

    행은 잡 시작 즉시 RUNNING 으로 생기고(begin), 지문 계산이 끝나면 DONE(finish). 1GB 대
    파일의 sha256 은 수 초라 짧지만, 실패·중단이 `sources.status` 에 남는 경로를 다운로드와
    같게 맞춘다.
    """
    try:
        path = media.resolve(deps.cfg, name)
    except media.MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(400, f"파일이 없다: {name}")
    language = _language(body.language)

    def work() -> dict:
        with connect() as conn:
            begun = ingest.begin_source(
                conn, deps.cfg, path, body.title or path.stem, "LECTURE", body.origin, body.context, language
            )
            if begun.reused:
                raise ingest.IngestError(f"이미 등록된 원본이다 (source {begun.source_id})")
            ingest.finish_source(conn, deps.cfg, begun.source_id, path)
        return {"sourceId": begun.source_id}

    return submit("register", name, work)


@router.post("/sources/from-url")
def add_source_from_url(body: UrlIn) -> dict:
    """URL 로 받아서 바로 원본으로 등록한다.

    🔴 호스트는 유튜브로 제한된다(download.check_url) — 임의 URL 을 서버가 받게 하면
    내부망을 찌를 수 있다.

    순서: probe(메타데이터) → 행을 RUNNING 으로(begin) → 다운로드 → 길이·지문 채우고 DONE(finish).
    다운로드가 수 분이라, 행이 먼저 있어야 화면에 "받는 중"이 보이고 서버가 죽으면 FAILED 로
    정리된다(api.serve). 같은 URL 을 다시 넣으면 DONE 행은 재사용, FAILED 행은 같은 id 로 재시도.
    """
    try:
        download.check_url(body.url)
    except download.DownloadError as exc:
        # 잡을 띄우기 전에 막는다. 큐에 넣고 실패하면 사용자는 한참 뒤에야 이유를 안다.
        raise HTTPException(400, str(exc)) from exc
    language = _language(body.language)

    def work() -> dict:
        info = download.probe(body.url)
        target = download.target_path(deps.cfg, info)
        with connect() as conn:
            begun = ingest.begin_source(
                conn, deps.cfg, target, info.title, "LECTURE",
                f"{info.url} ({info.uploader})", body.context, language,
            )
        if begun.reused:
            return {"sourceId": begun.source_id, "reused": True, "title": info.title}
        try:
            download.fetch_video(deps.cfg, info)
        except Exception as exc:
            with connect() as conn:
                ingest.fail_source(conn, begun.source_id, f"{type(exc).__name__}: {exc}")
            raise
        with connect() as conn:
            ingest.finish_source(conn, deps.cfg, begun.source_id, target)
        return {"sourceId": begun.source_id, "reused": False, "title": info.title}

    return submit("download", body.url, work)


@router.post("/sources/{source_id}/chunks")
def add_chunk(source_id: int, body: ChunkIn) -> dict:
    # 이미 있는데 replace 가 아니면 큐에 넣기 전에 거절한다 — 기다린 뒤에 알면 늦다.
    with connect() as conn:
        exists = conn.execute("select 1 from chunks where source_id = ?", (source_id,)).fetchone()
    if exists and not body.replace:
        raise HTTPException(409, "청크가 이미 있습니다. 다시 뽑으려면 replace 를 켭니다")

    def work() -> dict:
        with connect() as conn:
            chunk_id = ingest.add_chunk(
                conn, deps.cfg, source_id, body.startSec, body.endSec, replace=body.replace
            )
        return {"chunkId": chunk_id}

    return submit("chunk", f"source {source_id}", work)


@router.post("/chunks/{chunk_id}/stt")
def run_stt(chunk_id: int, body: SttIn) -> dict:
    def work() -> dict:
        with connect() as conn:
            result = stt.run_for_chunk(
                conn, deps.cfg, chunk_id, body.model, body.force, body.initialPrompt, body.language
            )
        return {"utterances": len(result.rows), "transcribeMs": result.transcribe_ms, "model": result.model}

    return submit("stt", f"chunk {chunk_id}", work)


@router.post("/chunks/{chunk_id}/segment")
def run_segment(chunk_id: int, force: bool = False) -> dict:
    def work() -> dict:
        with connect() as conn:
            specs = segmentation.run_for_chunk(conn, deps.cfg, chunk_id, force)
        return {"segments": len(specs)}

    return submit("segment", f"chunk {chunk_id}", work)


@router.post("/sources/{source_id}/rank")
def run_rank(source_id: int, body: RankIn) -> dict:
    def work() -> dict:
        with connect() as conn:
            run_id = ranking.run_for_source(conn, deps.cfg, source_id, body.criteria)
        return {"runId": run_id}

    return submit("rank", f"source {source_id}", work)


@router.post("/runs/{run_id}/segments/{segment_id}/cut")
def run_cut(
    run_id: int, segment_id: int, score: float | None = None, replace: bool = False
) -> dict:
    # 이미 있는지는 잡을 띄우기 전에 본다 — 큐에 넣고 나서 실패하면 사용자는 한참 기다린 뒤에야 안다.
    with connect() as conn:
        if not replace and cutting.existing_clip(conn, run_id, segment_id) is not None:
            raise HTTPException(409, "이 구간으로 만든 클립이 이미 있습니다")

    def work() -> dict:
        with connect() as conn:
            clip_id = cutting.run_for_segment(conn, deps.cfg, run_id, segment_id, score, None, replace)
        return {"clipId": clip_id}

    return submit("cut", f"segment {segment_id}", work)


@router.post("/clips/{clip_id}/render")
def run_render(clip_id: int, force: bool = False, subtitles: bool = True) -> dict:
    def work() -> dict:
        with connect() as conn:
            out = render.run_for_clip(conn, deps.cfg, clip_id, force, subtitles)
        return {"path": str(out)}

    return submit("render", f"clip {clip_id}", work)


@router.post("/sources/{source_id}/pipeline")
def run_pipeline(source_id: int, body: RankIn, resegment: bool = False) -> dict:
    """[3]→[5]→[6]→[7] 을 한 잡으로. 구간이 이미 있으면 다시 나누지 않는다(§3)."""

    def work() -> dict:
        with connect() as conn:
            return pipeline.run_all(conn, deps.cfg, source_id, body.criteria, resegment)

    return submit("pipeline", f"source {source_id}", work)


@router.get("/cost")
def total_cost() -> dict:
    with connect() as conn:
        return pricing.estimate(rows(conn, "select * from stage_calls"), deps.cfg)


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = deps.queue.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job.as_dict()


@router.get("/jobs")
def list_jobs() -> list[dict]:
    return [j.as_dict() for j in deps.queue.recent()]


# ---------------------------------------------------------------- 결과물

@router.get("/clips/{clip_id}/file")
def get_clip_file(clip_id: int) -> Any:
    """스튜디오 프리뷰용. 발행 여부를 보지 않는다 — 시청자용은 M3 의 `/api/watch/clips/{id}/file`
    이고 그쪽은 `published_at` 이 있어야 준다(tease I1)."""
    with connect() as conn:
        clip = conn.execute("select path, rendered from clips where id = ?", (clip_id,)).fetchone()
    if clip is None or not clip["rendered"] or not clip["path"]:
        raise HTTPException(404, "clip not rendered")
    path = deps.cfg.work_file(clip["path"])
    if not path.is_file():
        raise HTTPException(404, "clip file missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/segments/{segment_id}/preview")
def get_segment_preview(segment_id: int) -> Any:
    """구간을 원본 화면비 그대로 잘라 보여준다.

    클립을 만들기 전에 "이 구간이 볼 만한가"를 눈으로 확인하는 용도다. 재인코딩 없이
    떠내므로 몇 초면 만들어지고, 한 번 만들면 캐시된다.
    TODO: stage_calls 에 'preview' 로 기록한다 — v9 의 stage CHECK 재생성과 함께(update_plan D8).
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

    out_dir = deps.cfg.work_dir / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"segment{segment_id:04d}.mp4"
    if not out.is_file():
        try:
            ffmpeg.copy_segment(
                str(deps.cfg.source_file(segment["source_path"])), str(out),
                float(segment["start_sec"]), float(segment["end_sec"]), deps.cfg.ffmpeg_bin,
            )
        except ffmpeg.FfmpegError as exc:
            raise HTTPException(500, f"미리보기 생성 실패: {exc}") from exc
    return FileResponse(out, media_type="video/mp4", filename=out.name)


@router.post("/clips/{clip_id}/review")
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
