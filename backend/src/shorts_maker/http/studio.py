"""스튜디오 API — 크리에이터(운영자)가 쓰는 `/api/**` 전부. **인증 필요.**

🔴 인증은 라우터 레벨로 걸려 있다(`router.dependencies`). 여기 `@router.get/post` 로 등록하는
라우트는 무엇이든 `require_auth` 를 거친다 — 엔드포인트마다 `Depends` 를 반복하지 않고,
그래서 빠뜨릴 수도 없다. `tests/test_api_auth.py` 가 이 라우터의 모든 라우트를 순회하며
무인증 401 을 확인한다.

시청자용 공개 API(`/api/watch/**`, tease §7-1) 는 M3 에서 **별도 라우터**(watch.py) 로
붙는다. 여기 넣으면 인증이 걸려 시청자가 못 쓰고, 저기 넣을 걸 여기 넣는 실수는 위 테스트가
잡지 못한다 — 공개 라우터에는 발행된 것만 읽는 조건이 따로 있다.

`deps.cfg` 는 **속성으로** 읽는다(`deps.cfg.xxx`). 값을 import 해 오면 테스트의 교체가
반영되지 않는다(deps.py 주석).
"""

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import pricing, standards
from ..adapters import ytdlp, ffmpeg
from ..answers import answer, clusters, events, report, suggest
from . import auth, deps, prompt_catalog
from ..pipeline import (
    cutting, ingest, media, orchestrate, overview, ranking, render, segmentation, stt,
)
from ..db import store
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
    """등록된 영상과 **각각 어디까지 왔는지**.

    홈 화면이 카드 하나에 진행 상황을 그리는 데 쓴다. 영상마다 상세를 따로 부르면 N+1 이 되고,
    영상이 열 개만 돼도 홈이 느려진다 — 세는 건 SQL 한 번이면 된다.
    """
    with connect() as conn:
        return rows(
            conn,
            """select s.*,
                      (select count(*) from chunks c where c.source_id = s.id) as chunk_count,
                      (select count(*) from utterances u join chunks c on c.id = u.chunk_id
                        where c.source_id = s.id) as utterance_count,
                      (select count(*) from segments sg join chunks c on c.id = sg.chunk_id
                        where c.source_id = s.id) as segment_count,
                      (select count(*) from questions q where q.source_id = s.id) as question_count,
                      (select count(*) from question_clusters qc
                        where qc.source_id = s.id and qc.status = 'OPEN') as open_cluster_count,
                      (select count(*) from clips cl join runs r on r.id = cl.run_id
                        where r.source_id = s.id) as clip_count,
                      (select count(*) from clips cl join runs r on r.id = cl.run_id
                        where r.source_id = s.id and cl.published_at is not null) as published_clip_count
               from sources s order by s.id desc""",
        )


@router.get("/sources/{source_id}")
def get_source(source_id: int) -> dict:
    with connect() as conn:
        found = conn.execute("select * from sources where id = %s", (source_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "source not found")
        chunks = rows(conn, "select * from chunks where source_id = %s order by idx", (source_id,))
        for chunk in chunks:
            chunk["utteranceCount"] = conn.execute(
                "select count(*) as n from utterances where chunk_id = %s", (chunk["id"],)
            ).fetchone()["n"]
            chunk["segments"] = rows(
                conn, "select * from segments where chunk_id = %s order by idx", (chunk["id"],)
            )
        # runs.ranked 는 jsonb 라 psycopg 가 dict 로 돌려준다 — SQLite 시절의 json.loads 는 사라졌다.
        runs = rows(conn, "select * from runs where source_id = %s order by id desc", (source_id,))
        clips = rows(
            conn,
            # 🔴 클립이 **어디서 나왔는지**를 함께 준다. 질문에 답한 것과 크리에이터가 자기 기준으로
            # 뽑은 것이 한 목록에 섞이면 "클립 3" 이라는 이름만으로는 구분할 수가 없다.
            # 질문에서 나온 것은 대표 문장이, 기준에서 나온 것은 그때 쓴 기준 문장이 이름이 된다.
            """select cl.*, sg.description,
                      r.criteria_prompt, r.route,
                      qc.canonical_text as question,
                      (select count(*) from questions q where q.cluster_id = qc.id) as asked_by
               from clips cl
               join segments sg on sg.id = cl.segment_id
               join chunks ch on ch.id = sg.chunk_id
               join runs r on r.id = cl.run_id
               left join question_clusters qc on qc.id = cl.question_cluster_id
               where ch.source_id = %s order by cl.id desc""",
            (source_id,),
        )
        for clip in clips:
            clip["reviews"] = rows(
                conn,
                "select * from clip_reviews where clip_id = %s order by id desc",
                (clip["id"],),
            )
        # 🔴 `select *` 를 쓰지 않는다. prompt·response 는 한 건이 수십 KB 라 화면 한 번에 수 MB 가
        # 실린다(마이그레이션 005). 본문은 사용자가 그 행을 펼칠 때만 따로 읽는다 —
        # `has_body` 로 펼칠 것이 있는지만 알려 준다.
        calls = rows(
            conn,
            """select id, source_id, run_id, segment_id, stage, model,
                      input_tokens, output_tokens, thinking_tokens, total_tokens, cached_tokens,
                      latency_ms, params, error, created_at,
                      (prompt is not null or response is not null) as has_body
               from stage_calls where source_id = %s order by id desc""",
            (source_id,),
        )
        return {
            # 🔴 긴 작업이 도는 중인지. 화면이 "다시 추출" 을 잠그는 근거다 — 전사 중에 청크를
            # 지우면 그 전사가 외래키 위반으로 죽는다(2026-09-06에 당했다).
            "busy": store.source_is_busy(conn, source_id),
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
            " where chunk_id = %s order by idx",
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
    """분석할 **범위**. 비우면 영상 전체다 — 조각을 몇 개로 나눌지는 코드가 정한다."""

    startSec: float | None = None
    endSec: float | None = None
    # 🔴 기존 청크와 그 아래 전부(발화·구간·클립·run)를 지우고 다시 만든다. 화면의 "다시 추출".
    replace: bool = False


class RankIn(BaseModel):
    criteria: str | None = None


class ReviewIn(BaseModel):
    verdict: str
    note: str | None = None


class ClipPatchIn(BaseModel):
    """숏폼 제목. 시청자 목록에서 보이는 문장이라 크리에이터가 고칠 수 있어야 한다."""

    title: str = Field(min_length=1, max_length=200)


class StandardIn(BaseModel):
    """관리자 기준. 🔴 정확한 상한(1000자)은 앞뒤 공백을 걷어낸 뒤 `standards.save` 가 본다 — 여기서
    잘라 버리면 붙여 넣으며 딸려 온 공백 때문에 멀쩡한 글이 거절된다. 이 상한은 터무니없는 입력만 막는다."""

    body: str = Field(default="", max_length=standards.MAX_CHARS * 2)


class SourcePatchIn(BaseModel):
    """사람이 고치는 원본 값 — 제목 · 영상 개요 · 유튜브 id.

    🔴 개요는 500자 상한. 이 글은 구간 분할·순위·자르기·후보 생성 **모든 호출에 붙는다.** 영상이
    무엇인지 한두 문장이면 되고, 길게 쓰면 대사보다 개요가 프롬프트를 더 차지한다.

    🔴 **보내지 않은 필드는 건드리지 않는다.** 셋이 한 폼에 있어도 고친 것만 오는 경우가 있고,
    제목만 바꾸려던 요청이 개요를 지우면 안 된다(`model_fields_set` 으로 가른다).
    """

    title: str | None = Field(default=None, max_length=300)
    context: str | None = Field(default=None, max_length=500)
    youtubeId: str | None = Field(default=None, max_length=200)


class ClusterPatchIn(BaseModel):
    """대표 문장 수정과 상태 변경. 둘 다 선택이고, 둘 다 없으면 400."""

    canonicalText: str | None = None
    status: str | None = None


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


@router.post("/sources/{source_id}/publish")
def publish_source(source_id: int) -> dict:
    """시청자 화면(`/watch`)의 목록에 이 영상을 노출한다.

    🔴 발행하지 않은 영상은 공개 API 에서 **404** 다 — 목록에서 빠지는 게 아니라 존재를 알리지
    않는다(watch.py). 그래서 이 토글이 시청자 면 전체의 스위치다.

    아직 처리 중(RUNNING)이거나 실패한 영상은 발행하지 않는다 — 길이도 모르는 영상이 목록에
    뜬다. 임베드 id 가 없어도 발행은 막지 않는다(질문만 받는 영상이 있을 수 있다). 화면이 경고한다.
    """
    with connect() as conn:
        found = conn.execute("select status from sources where id = %s", (source_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "source not found")
        if found["status"] != "DONE":
            raise HTTPException(409, f"등록이 끝나지 않았습니다 (status={found['status']})")
        conn.execute("update sources set published = true, updated_at = now() where id = %s", (source_id,))
        conn.commit()
    return {"published": True}


@router.post("/sources/{source_id}/unpublish")
def unpublish_source(source_id: int) -> dict:
    """목록에서 내린다. 쌓인 질문과 좋아요는 그대로 남는다 — 다시 발행하면 그대로 보인다."""
    with connect() as conn:
        if conn.execute("select 1 from sources where id = %s", (source_id,)).fetchone() is None:
            raise HTTPException(404, "source not found")
        conn.execute("update sources set published = false, updated_at = now() where id = %s", (source_id,))
        conn.commit()
    return {"published": False}


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

    🔴 호스트는 유튜브로 제한된다(ytdlp.check_url) — 임의 URL 을 서버가 받게 하면
    내부망을 찌를 수 있다.

    순서: probe(메타데이터) → 행을 RUNNING 으로(begin) → 다운로드 → 길이·지문 채우고 DONE(finish).
    다운로드가 수 분이라, 행이 먼저 있어야 화면에 "받는 중"이 보이고 서버가 죽으면 FAILED 로
    정리된다(server.serve). 같은 URL 을 다시 넣으면 DONE 행은 재사용, FAILED 행은 같은 id 로 재시도.
    """
    try:
        ytdlp.check_url(body.url)
    except ytdlp.DownloadError as exc:
        # 잡을 띄우기 전에 막는다. 큐에 넣고 실패하면 사용자는 한참 뒤에야 이유를 안다.
        raise HTTPException(400, str(exc)) from exc
    language = _language(body.language)

    def work() -> dict:
        info = ytdlp.probe(body.url)
        target = ytdlp.target_path(deps.cfg, info)
        with connect() as conn:
            begun = ingest.begin_source(
                conn, deps.cfg, target, info.title, "LECTURE",
                f"{info.url} ({info.uploader})", body.context, language,
                youtube_id=info.video_id, channel=info.uploader or None,
            )
        if begun.reused:
            return {"sourceId": begun.source_id, "reused": True, "title": info.title}
        # 🔴 다운로드도 stage_calls 에 남긴다(규약: 모든 단계). 실제 제약은 비용이 아니라 시간이고,
        # 1.3GB 를 서버 회선으로 받는 데 몇 분이 드는지는 여기 말고는 아무 데도 안 남는다.
        started = time.monotonic()
        try:
            ytdlp.fetch_video(deps.cfg, info)
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    "insert into stage_calls (source_id, stage, latency_ms, params, error)"
                    " values (%s, 'download', %s, %s, %s)",
                    (begun.source_id, int((time.monotonic() - started) * 1000),
                     Jsonb({"url": info.url}), f"{type(exc).__name__}: {exc}"),
                )
                ingest.fail_source(conn, begun.source_id, f"{type(exc).__name__}: {exc}")
            raise
        with connect() as conn:
            conn.execute(
                "insert into stage_calls (source_id, stage, latency_ms, params) values (%s, 'download', %s, %s)",
                (begun.source_id, int((time.monotonic() - started) * 1000), Jsonb({"url": info.url})),
            )
            conn.commit()
            ingest.finish_source(conn, deps.cfg, begun.source_id, target)
        return {"sourceId": begun.source_id, "reused": False, "title": info.title}

    return submit("download", body.url, work)


@router.post("/sources/{source_id}/chunks")
def add_chunks(source_id: int, body: ChunkIn) -> dict:
    """영상을 통째로 나눠 분석용 오디오를 뽑는다. 화면의 "구간 추출" 한 번.

    사용자가 정하는 건 **범위**(어디부터 어디까지 분석할까)다. 그 안을 몇 조각으로 나눌지는
    코드가 정한다 — 청크는 취향이 아니라 **메모리 상한**이기 때문이다(ingest.plan_chunks).
    """
    # 이미 있는데 replace 가 아니면 큐에 넣기 전에 거절한다 — 기다린 뒤에 알면 늦다.
    with connect() as conn:
        exists = conn.execute("select 1 from chunks where source_id = %s", (source_id,)).fetchone()
    if exists and not body.replace:
        raise HTTPException(409, "구간이 이미 추출돼 있습니다. 다시 뽑으려면 replace 를 켭니다")

    def work() -> dict:
        with connect() as conn:
            made = ingest.add_chunks(
                conn, deps.cfg, source_id, body.startSec, body.endSec, replace=body.replace
            )
        return {"chunks": len(made)}

    return submit("chunk", f"source {source_id}", work)


@router.post("/sources/{source_id}/stt")
def run_stt(source_id: int, body: SttIn) -> dict:
    """영상 전체를 전사한다. 청크가 여럿이면 순서대로 — 화면에는 하나의 작업으로 보인다."""
    language = _language(body.language)

    def work() -> dict:
        with connect() as conn:
            results = stt.run_for_source(
                conn, deps.cfg, source_id, body.model, body.force, body.initialPrompt, language
            )
        return {
            "chunks": len(results),
            "utterances": sum(len(r.rows) for r in results),
            "transcribeMs": sum(r.transcribe_ms for r in results),
            "model": results[0].model if results else None,
        }

    return submit("stt", f"source {source_id}", work)


@router.post("/sources/{source_id}/segment")
def run_segment(source_id: int, force: bool = False) -> dict:
    """영상 전체를 주제 단위로 나눈다. 🔴 구간 번호는 소스 안에서 연속이다(segmentation 주석).

    이미 끝난 조각은 건너뛴다 — 중간에 실패했을 때 다시 눌러도 앞부분을 새로 하지 않는다.
    `force` 면 전부 다시 만든다.
    """

    def work() -> dict:
        with connect() as conn:
            segments = segmentation.run_for_source(conn, deps.cfg, source_id, force)
        return {"segments": len(segments)}

    return submit("segment", f"source {source_id}", work)


@router.post("/sources/{source_id}/overview")
def draft_overview(source_id: int) -> dict:
    """구간 요약을 읽어 영상 개요 **초안**을 쓴다(LLM 1회).

    🔴 **저장하지 않는다.** 초안을 잡 결과로 돌려주고 멈춘다 — 이 글은 이후 모든 호출에 붙어서
    틀린 한 줄이 조용히 파이프라인 전체에 퍼진다. 사람이 읽고 `PATCH /api/sources/{id}` 로 넣는다.

    잡 큐를 지나는 이유는 이 레포의 다른 LLM 호출과 같다 — 워커가 하나라 동시 1건이고, 도는 잡이
    있으면 409 다. 호출 하나는 몇 초지만 예외를 두면 그 예외가 다음 예외의 근거가 된다.

    🔴 전제는 **잡을 띄우기 전에** 본다(`add_source_from_url` 과 같은 이유). 큐에 넣고 실패하면
    사용자는 배너가 빨개진 뒤에야 "구간이 아직 없다" 를 안다.
    """
    with connect() as conn:
        found = conn.execute("select 1 from sources where id = %s", (source_id,)).fetchone()
        if found is None:
            raise HTTPException(404, "source not found")
        ready = conn.execute(
            "select count(*) as n from segments sg join chunks ch on ch.id = sg.chunk_id"
            " where ch.source_id = %s and sg.description is not null",
            (source_id,),
        ).fetchone()["n"]
    if not ready:
        # 409 — 요청은 옳고 지금 상태와 충돌할 뿐이다(발행 거절과 같은 자리).
        raise HTTPException(409, "구간 요약이 아직 없습니다 — 주제 분할을 먼저 끝내 주세요")

    def work() -> dict:
        with connect() as conn:
            return {"context": overview.draft(conn, deps.cfg, source_id)}

    return submit("overview", f"source {source_id}", work)


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
            return orchestrate.run_all(conn, deps.cfg, source_id, body.criteria, resegment)

    return submit("pipeline", f"source {source_id}", work)


# ---------------------------------------------------------------- 질문 묶기

@router.post("/sources/{source_id}/aggregate")
def aggregate_questions(source_id: int) -> dict:
    """**[집계]** — 미분류 질문을 묶는다. 🔴 크리에이터가 누를 때만 돈다(update_plan D11).

    질문이 들어올 때마다 부르지 않는 이유는 둘이다: 공개 경로에서 요청마다 유료 API 를 부르면
    그게 공격면이고, 제품 정의상 "취합"은 크리에이터의 행동이다.

    LLM 은 대표 문장만 짓고, 소속은 임베딩 코사인이, 개수는 SQL 이 정한다(answers/clusters.py).
    재집계는 증분이라 몇 번을 눌러도 기존 소속과 좋아요가 그대로다.
    """

    def work() -> dict:
        with connect() as conn:
            if conn.execute("select 1 from sources where id = %s", (source_id,)).fetchone() is None:
                raise clusters.ClusterError(f"source {source_id} 없음")
            return clusters.aggregate(conn, deps.cfg, source_id)

    return submit("cluster", f"source {source_id}", work)


@router.get("/sources/{source_id}/clusters")
def list_clusters(source_id: int) -> dict:
    """클러스터(수요 순) + 각 클러스터의 질문 원문 + 아직 안 묶인 질문."""
    with connect() as conn:
        if conn.execute("select 1 from sources where id = %s", (source_id,)).fetchone() is None:
            raise HTTPException(404, "source not found")
        found = clusters.demand(conn, source_id)
        for cluster in found:
            cluster["questions"] = clusters.questions_of(conn, cluster["id"])
            # 이 묶음에 답한 클립과 judge 소견. 크리에이터가 발행 전에 봐야 하는 것들이다.
            cluster["clip"] = clusters.clip_of(conn, cluster["id"])
            # 🔴 겨룬 후보는 **대사와 함께** 온다. 클립보다 먼저 존재한다 — 무엇을 만들지
            # 고르는 것이 크리에이터의 일이기 때문이다(answers/answer.py 머리 주석).
            cluster["candidates"] = clusters.candidates_of(conn, cluster["run_id"])
        return {"clusters": found, "unclustered": clusters.unclustered(conn, source_id)}


@router.get("/sources/{source_id}/insights")
def source_insights(source_id: int) -> dict:
    """숏폼별 퍼널 — 재생 → 완주 → CTA → 원본 이동 → 원본 재생 (tease §9, update_plan M6b).

    이 화면이 존재하는 이유는 이 제품의 주장을 데이터로 뒷받침하는 것이다. "숏폼이 원본
    유입을 만든다"는 `viewer_events` 에 쌓인 행 없이는 말할 수 없다.

    🔴 세는 단위는 **사람 수**이고 비율은 계산하지 않는다 — 근거는 `events.funnel` 주석.
    """
    with connect() as conn:
        if conn.execute("select 1 from sources where id = %s", (source_id,)).fetchone() is None:
            raise HTTPException(404, "source not found")
        return events.funnel(conn, source_id)


@router.get("/sources/{source_id}/report")
def source_report(source_id: int) -> dict:
    """**회차 리포트** — 이 설명회가 답한 것과 답하지 않은 것 (기획서 §05-③).

    🔴 대시보드가 아니라 **산출물**이다. 크리에이터가 다음 설명회의 큐시트로 쓰고, 공고에서
    빠진 정보를 채우는 데 쓴다. 그래서 화면은 복사해서 가져갈 수 있게 그린다.
    """
    with connect() as conn:
        try:
            return report.build(conn, deps.cfg, source_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.patch("/clusters/{cluster_id}")
def patch_cluster(cluster_id: int, body: ClusterPatchIn) -> dict:
    """대표 문장을 고치거나 상태를 바꾼다.

    🔴 상태 변경은 `clusters.transition()` 만 거친다 — 허용 표에 없는 전이는 여기서 400 이다.
    대표 문장을 고치면 벡터를 다시 계산한다(안 하면 다음 집계가 옛 문장 기준으로 붙인다).
    """
    if body.canonicalText is None and body.status is None:
        raise HTTPException(400, "canonicalText 나 status 중 하나는 있어야 합니다")
    with connect() as conn:
        try:
            row = None
            if body.canonicalText is not None:
                row = clusters.rename(conn, deps.cfg, cluster_id, body.canonicalText)
            if body.status is not None:
                row = clusters.transition(conn, cluster_id, body.status)
                conn.commit()
        except clusters.ClusterError as exc:
            raise HTTPException(400, str(exc)) from exc
    return row or {}


@router.post("/clusters/{cluster_id}/answer")
def answer_cluster(cluster_id: int) -> dict:
    """**[답하기]** — 이 질문에 답할 **후보들을 만든다.** 숏폼은 아직 만들지 않는다.

    라우팅 + 구간 선택 → 후보 제안 → judge 병렬 판정 → 후보 저장. 고정 DAG 다
    (answers/answer.py). 실패하면 클러스터가 OPEN 으로 돌아와 다시 누를 수 있다.

    🔴 여기서 멈추는 이유: 무엇을 숏폼으로 만들지는 크리에이터가 **대사를 읽고** 고른다.
    다음 단계는 `POST /api/candidates/{id}/build` 다.
    """

    def work() -> dict:
        with connect() as conn:
            return answer.run(conn, deps.cfg, cluster_id)

    return submit("answer", f"cluster {cluster_id}", work)


@router.post("/candidates/{candidate_id}/build")
def build_candidate(candidate_id: int) -> dict:
    """**[이걸로 만들기]** — 고른 후보를 컷하고 렌더한다.

    🔴 LLM 을 부르지 않는다. 범위도 대사도 판정도 이미 있고 드는 건 ffmpeg 시간뿐이다 —
    그래서 마음을 바꿔 다른 후보를 골라도 추가 비용이 없다. 다시 고르면 그 run 의 기존 클립을
    지우고 새로 만든다(한 질문에 답하는 클립은 하나다, 불변식 I1).
    """

    def work() -> dict:
        with connect() as conn:
            return answer.build(conn, deps.cfg, candidate_id)

    return submit("render", f"candidate {candidate_id}", work)


@router.patch("/clips/{clip_id}")
def patch_clip(clip_id: int, body: ClipPatchIn) -> dict:
    """제목을 고친다.

    🔴 기본값은 질문(답하기 경로) 또는 구간 설명(기준 경로)이지만 둘 다 제목으로 쓰라고 쓴 문장이
    아니다. 시청자 목록에 그대로 보이므로 고칠 길이 있어야 한다.
    """
    # 🔴 공백만 있는 제목은 min_length 를 통과한다. 그대로 저장하면 빈 문자열이 남아 목록에서
    # 제목 자리가 사라지고, "제목이 없는 것"과 "빈 제목"이 구분되지 않는다.
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "제목을 입력하세요")
    with connect() as conn:
        row = conn.execute(
            "update clips set title = %s where id = %s returning id, title",
            (title, clip_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "clip not found")
        conn.commit()
    return dict(row)


@router.post("/clips/{clip_id}/publish")
def publish_clip(clip_id: int) -> dict:
    """🔴 발행하면 이 클립이 시청자에게 보인다(`/api/watch/clips/{id}/file`).

    렌더되지 않은 클립은 발행하지 않는다 — 목록에 떴는데 재생이 안 되는 게 최악이다.
    """
    with connect() as conn:
        clip = conn.execute(
            "select rendered, question_cluster_id from clips where id = %s", (clip_id,)
        ).fetchone()
        if clip is None:
            raise HTTPException(404, "clip not found")
        if not clip["rendered"]:
            raise HTTPException(409, "아직 렌더되지 않은 클립입니다")
        conn.execute(
            "update clips set published_at = now() where id = %s and published_at is null",
            (clip_id,),
        )
        if clip["question_cluster_id"]:
            try:
                clusters.transition(conn, clip["question_cluster_id"], "PUBLISHED")
            except clusters.ClusterError as exc:
                # 이미 PUBLISHED 인 클러스터를 다시 발행하는 건 오류가 아니다 — 클립만 갱신한다.
                if "REVIEW" not in str(exc):
                    raise HTTPException(400, str(exc)) from exc
        conn.commit()
        # 🔴 발행이 **먼저** 커밋됐다. 추천용 색인(update_plan D13)이 실패해도 발행은 되돌리지 않는다 —
        # 크리에이터의 행동을 임베딩 할당량에 묶지 않는다. 빠진 것은 `sm answers index-clips` 로 채운다.
        try:
            indexed = suggest.index_clip(conn, deps.cfg, clip_id) == "indexed"
            conn.commit()
        except Exception:
            conn.rollback()
            indexed = False
    return {"published": True, "indexed": indexed}


@router.post("/clips/{clip_id}/unpublish")
def unpublish_clip(clip_id: int) -> dict:
    """시청자 화면에서 내린다. 클립과 판정 이력은 남는다."""
    with connect() as conn:
        clip = conn.execute(
            "select question_cluster_id from clips where id = %s", (clip_id,)
        ).fetchone()
        if clip is None:
            raise HTTPException(404, "clip not found")
        conn.execute("update clips set published_at = null where id = %s", (clip_id,))
        if clip["question_cluster_id"]:
            try:
                clusters.transition(conn, clip["question_cluster_id"], "REVIEW")
            except clusters.ClusterError:
                pass  # 이미 REVIEW 이하면 그대로 둔다
        conn.commit()
    return {"published": False}


@router.get("/prompts")
def list_prompts() -> dict:
    """프롬프트 목록 — 무엇이 고정(1층)이고 무엇을 누가 채우는가.

    🔴 1층 규칙은 **보여주기만** 한다. 출력 형식이 한 글자만 어긋나도 파싱이 실패하고, 자립성 관문이
    흔들리면 앞뒤를 모르면 이해 못 하는 클립이 조용히 발행된다. 원문은 코드에서 그대로 읽는다
    (`http/prompt_catalog.py`).
    """
    with connect() as conn:
        return prompt_catalog.build(conn)


@router.put("/prompts/standard")
def put_standard(body: StandardIn) -> dict:
    """관리자 기준을 저장한다. **덧붙인다** — 고친 이력이 남는다(마이그레이션 007).

    빈 글을 보내면 기준이 지워진다. 다음 답하기·클립 만들기부터 반영되고 이미 만든 것은 그대로다.
    """
    with connect() as conn:
        try:
            row = standards.save(conn, body.body)
        except standards.StandardError as exc:
            raise HTTPException(422, str(exc)) from exc
        conn.commit()
    return {"body": row["body"], "updatedAt": row["created_at"], "maxChars": standards.MAX_CHARS}


@router.patch("/sources/{source_id}")
def patch_source(source_id: int, body: SourcePatchIn) -> dict:
    """제목 · 영상 개요 · 유튜브 id 를 고친다.

    🔴 개요는 **이미 나눈 구간에는 반영되지 않는다.** 구간 분할도 개요를 쓰지만 구간은 캐시된
    자산이라 다시 나누기 전까지 그대로다. 이후 순위·자르기·후보 생성에는 바로 반영된다.

    🔴 **제목이 왜 고칠 수 있어야 하나**: 파일로 등록한 원본의 제목은 파일 이름이다
    (`media/{name}/register` 가 `path.stem` 을 쓴다). 그게 시청자 화면에 그대로 나가서
    `MVqTWMg4n0o` 같은 것이 사람 앞에 보였다(2026-09-19).

    🔴 **유튜브 id 도 같은 이유다.** 파일로 등록한 원본에는 id 가 없어 임베드가 막히는데,
    예전에는 그걸 채울 길이 psql 밖에 없었다. URL 을 통째로 붙여넣어도 id 만 뽑아 저장한다.
    """
    # 보낸 것만 담는다 — null 을 보내 지우는 것과, 아예 안 보낸 것은 뜻이 다르다.
    updates: dict[str, str | None] = {}
    if "title" in body.model_fields_set:
        title = (body.title or "").strip()
        if not title:
            # 🔴 제목은 비울 수 없다. 목록과 시청자 화면이 이 값으로 영상을 가리킨다.
            raise HTTPException(400, "제목은 비울 수 없습니다")
        updates["title"] = title
    if "context" in body.model_fields_set:
        updates["context"] = (body.context or "").strip() or None
    if "youtubeId" in body.model_fields_set:
        raw = (body.youtubeId or "").strip()
        if raw:
            try:
                updates["youtube_id"] = ytdlp.parse_video_id(raw)
            except ytdlp.DownloadError as exc:
                raise HTTPException(400, str(exc)) from exc
        else:
            updates["youtube_id"] = None
    if not updates:
        raise HTTPException(400, "고칠 값이 없습니다")

    # 컬럼 이름은 위에서 코드가 정한 것만 들어온다 — 사용자 입력이 식별자 자리에 오지 않는다.
    assignments = ", ".join(f"{column} = %s" for column in updates)
    with connect() as conn:
        row = conn.execute(
            f"update sources set {assignments}, updated_at = now() where id = %s"
            " returning id, title, context, youtube_id",
            (*updates.values(), source_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "source not found")
        conn.commit()
    return dict(row)


@router.get("/cost")
def total_cost() -> dict:
    with connect() as conn:
        # 🔴 비용 계산에 필요한 컬럼만 읽는다. `select *` 면 전 기록의 프롬프트 본문을 통째로
        # 끌어와 메모리에 올린다 — 비용 숫자 하나 보려고 수백 MB 를 읽을 수 있다.
        return pricing.estimate(
            rows(conn, "select stage, model, input_tokens, output_tokens, thinking_tokens,"
                       " total_tokens, cached_tokens from stage_calls"),
            deps.cfg,
        )


@router.get("/stage-calls/{call_id}")
def get_stage_call_body(call_id: int) -> dict:
    """그 호출의 **프롬프트와 원본 응답**. 사용자가 단계 기록의 한 행을 펼칠 때만 읽는다.

    🔴 이게 있는 이유: 판정이 이상해 보일 때 "모델이 무엇을 보고 무엇을 답했는지" 를 확인할
    방법이 없었다. 같은 호출을 다시 하는 것뿐이었고, 무료 등급에서는 그 재현이 할당량을 깎는다.
    """
    with connect() as conn:
        row = conn.execute(
            """select id, stage, model, created_at, prompt, response, error
               from stage_calls where id = %s""",
            (call_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "stage call not found")
    return dict(row)


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
        clip = conn.execute("select path, rendered from clips where id = %s", (clip_id,)).fetchone()
    if clip is None or not clip["rendered"] or not clip["path"]:
        raise HTTPException(404, "clip not rendered")
    path = deps.cfg.work_file(clip["path"])
    if not path.is_file():
        raise HTTPException(404, "clip file missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/segments/{segment_id}/preview")
def get_segment_preview(segment_id: int, request: Request) -> Any:
    """구간을 원본 화면비 그대로 잘라 보여준다.

    클립을 만들기 전에 "이 구간이 볼 만한가"를 눈으로 확인하는 용도다. 재인코딩 없이
    떠내므로 몇 초면 만들어지고, 한 번 만들면 캐시된다. 새로 만들 때만 stage_calls 에
    'preview' 로 남긴다(캐시 히트는 단계가 아니다).
    """
    with connect() as conn:
        segment = conn.execute(
            """select sg.*, s.id as source_id, s.path as source_path from segments sg
               join chunks ch on ch.id = sg.chunk_id
               join sources s on s.id = ch.source_id
               where sg.id = %s""",
            (segment_id,),
        ).fetchone()
    if segment is None:
        raise HTTPException(404, "segment not found")

    out_dir = deps.cfg.work_dir / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"segment{segment_id:04d}.mp4"
    if not out.is_file():
        # 🔴 **미리보기를 만드는 건 행동이다.** ffmpeg 가 2vCPU 를 쓴다. 메서드가 GET 이라
        # 역할 검사(deps.SAFE_METHODS)를 그냥 통과하므로 여기서 한 번 더 막는다.
        # 이미 만들어 둔 것은 보기 전용도 본다 — 구경을 막으려는 게 아니라 부하를 막는 것이다.
        if deps.current_role(request) != auth.ADMIN:
            raise HTTPException(403, "보기 전용에서는 미리보기를 새로 만들 수 없습니다")
        started = time.monotonic()
        error: str | None = None
        try:
            ffmpeg.copy_segment(
                str(deps.cfg.source_file(segment["source_path"])), str(out),
                float(segment["start_sec"]), float(segment["end_sec"]), deps.cfg.ffmpeg_bin,
            )
        except ffmpeg.FfmpegError as exc:
            error = str(exc)
        with connect() as conn:
            conn.execute(
                "insert into stage_calls (source_id, segment_id, stage, latency_ms, error)"
                " values (%s, %s, 'preview', %s, %s)",
                (segment["source_id"], segment_id, int((time.monotonic() - started) * 1000), error),
            )
            conn.commit()
        if error is not None:
            raise HTTPException(500, f"미리보기 생성 실패: {error}")
    return FileResponse(out, media_type="video/mp4", filename=out.name)


@router.post("/clips/{clip_id}/review")
def add_review(clip_id: int, body: ReviewIn) -> dict:
    """사람 평가(쓸만함 / 아님). 🔴 **멱등이다** — 같은 평가를 여러 번 보내도 한 줄이다.

    2026-09-14 에 당했다: 버튼을 연달아 누르자 클립 하나에 "쓸만함" 14줄 · "아님" 4줄이 3초 안에
    쌓였다. 조건 없는 삽입이었다. 이 표는 사람·모델 판정 일치율(tease §11)의 입력이라, 행 단위로
    세면 14번 누른 클립이 14표가 된다.

    규칙: 그 클립의 **최신 사람 평가**와 평가·메모가 같으면 넣지 않고 지금 값을 돌려준다. 다르면
    넣는다 — **마음을 바꾼 기록은 남는다**(append-only, 읽을 때 최신 행). 모델 평가(`reviewer='llm'`)는
    비교 대상이 아니다. 사람이 판정에 동의하는 것도 한 표다.

    🔴 **클립 행을 잠그고 확인한다.** 확인과 삽입 사이에 틈이 있으면 거의 동시에 온 두 요청이 둘 다
    "아직 없음" 을 보고 둘 다 넣는다 — 연타가 정확히 그 경우다. `for update` 로 같은 클립의 평가 요청을
    한 줄로 세우면 뒤 요청은 앞 요청이 커밋한 행을 보고 건너뛴다.
    """
    if body.verdict not in ("OK", "NG"):
        raise HTTPException(400, "verdict must be OK or NG")
    # 빈 메모와 메모 없음은 같은 것이다. 구분하면 "" 와 null 이 서로 다른 평가로 쌓인다.
    note = (body.note or "").strip() or None
    with connect() as conn:
        if conn.execute("select id from clips where id = %s for update", (clip_id,)).fetchone() is None:
            raise HTTPException(404, "clip not found")
        latest = conn.execute(
            """select id, verdict, note from clip_reviews
               where clip_id = %s and reviewer = 'human' order by id desc limit 1""",
            (clip_id,),
        ).fetchone()
        if latest is not None and latest["verdict"] == body.verdict and latest["note"] == note:
            conn.commit()  # 잠금을 바로 푼다
            return {"ok": True, "created": False, "reviewId": latest["id"], "verdict": latest["verdict"]}
        row = conn.execute(
            "insert into clip_reviews (clip_id, verdict, note) values (%s, %s, %s) returning id",
            (clip_id, body.verdict, note),
        ).fetchone()
        conn.commit()
    return {"ok": True, "created": True, "reviewId": row["id"], "verdict": body.verdict}
