"""숏폼 편집 API — 템플릿 · 시리즈 · 클립 편집 명세 · 글자 층 미리보기(tease §5-10). **인증 필요.**

🔴 이 라우터는 **스튜디오 라우터의 하위 라우터**다(`studio.py` 의 `router.include_router(layouts.router)`).
접두사 `/api` 와 `require_auth` 는 거기서 붙는다 — 여기에는 적지 않고, 앱에 직접 붙이지도 않는다.

도메인 로직은 없다. 템플릿 검증 · 배치는 `pipeline/templates.py`, 명세 검증 · 기본값은 `pipeline/layout.py`,
글자 층은 `pipeline/card.py`, 명세를 템플릿 · 시리즈와 묶어 푸는 것은 `render.resolve` 가 한다 — 렌더와 이 화면이
**같은 길**로 풀어야 미리보기와 결과가 어긋나지 않는다.

보기 전용(readonly):
- 읽기(GET)는 된다. 편집 화면을 열고, 이미 그려 둔 글자 층과 원본을 본다.
- 🔴 미리보기를 **새로** 그리는 것은 ffmpeg 를 돌리는 행동이라 POST 다 — 메서드로 403 이 난다(deps.SAFE_METHODS).
  GET 으로 두고 그 자리에서 막는 구간 미리보기와 달리, 그려 둔 PNG 는 `GET /cards/{name}` 으로 따로 읽는다.
"""

import hashlib
from typing import Any

import psycopg
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import deps
from .deps import connect, rows
from ..adapters import ffmpeg
from ..pipeline import card, focus, layout as layouts, render, scenes, templates

# 🔴 접두사·의존성을 두지 않는다(모듈 머리 주석).
router = APIRouter()

BADGE_DIR = "badges"
# 배지는 높이 80px 로 줄여 쓴다. 2MB 면 스크린샷에서 오린 PNG 로 넉넉하다 — 그보다 크면 잘못 고른 파일이다.
BADGE_MAX_BYTES = 2 * 1024 * 1024
BADGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class LayoutIn(BaseModel):
    """화면이 보낸 명세 한 벌. 모양 검사는 `layout.validate` 가 한다 — 여기서 필드를 다시 적으면 둘이 어긋난다."""

    layout: dict[str, Any]


class SeriesIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    templateId: int | None = None


class SeriesPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    templateId: int | None = None
    # templateId 를 null 로 **비우려는** 것과 안 보낸 것을 가른다.
    clearTemplate: bool = False


# ---------------------------------------------------------------- 읽기 도우미

def _template_rows(conn: psycopg.Connection) -> list[dict]:
    """템플릿 목록. spec 이 잘못된 템플릿도 목록에는 싣되 `error` 를 붙인다 — 숨기면 왜 안 보이는지 모른다."""
    out = []
    for row in rows(conn, "select id, name, kind, origin, spec, updated_at from templates order by id"):
        try:
            spec = templates.validate(row["spec"])
            row |= {"spec": spec, "slotLimits": list(templates.slot_limits(spec)), "error": None}
        except templates.TemplateError as exc:
            row |= {"slotLimits": None, "error": str(exc)}
        out.append(row)
    return out


def _series_rows(conn: psycopg.Connection) -> list[dict]:
    out = []
    for row in rows(conn, "select id, name, template_id, badge_path from series order by id"):
        # 🔴 경로는 싣지 않는다 — 화면은 있나 없나와 읽을 주소만 알면 된다. 주소에 파일 이름을 붙여 바뀌면 새로 읽게 한다.
        path = row.pop("badge_path")
        row["badgeUrl"] = f"/api/series/{row['id']}/badge?v={path.rsplit('/', 1)[-1]}" if path else None
        out.append(row)
    return out


def _clip(conn: psycopg.Connection, clip_id: int) -> dict:
    clip = conn.execute(
        """select cl.id, cl.title, cl.start_sec, cl.end_sec, cl.total_sec, cl.rendered, cl.layout, cl.render_spec,
                  s.id as source_id, s.path as source_path, s.channel, s.series_id as source_series_id
           from clips cl
           join segments sg on sg.id = cl.segment_id
           join chunks ch on ch.id = sg.chunk_id
           join sources s on s.id = ch.source_id
           where cl.id = %s""",
        (clip_id,),
    ).fetchone()
    if clip is None:
        raise HTTPException(404, "clip not found")
    return dict(clip)


def _source_size(clip: dict) -> tuple[int, int]:
    path = deps.cfg.source_file(clip["source_path"])
    if not path.is_file():
        raise HTTPException(409, "원본 파일이 없어 편집할 수 없습니다")
    try:
        return ffmpeg.video_size(str(path))
    except ffmpeg.FfmpegError as exc:
        raise HTTPException(500, f"원본 크기를 읽지 못했습니다: {exc}") from exc


def _resolve(conn: psycopg.Connection, clip: dict, raw: dict) -> dict:
    try:
        return render.resolve(conn, deps.cfg, clip["source_path"], raw, render.clip_duration(clip))
    except render.RenderError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ffmpeg.FfmpegError as exc:
        raise HTTPException(500, f"원본을 읽지 못했습니다: {exc}") from exc


def _default(conn: psycopg.Connection, clip: dict, src: tuple[int, int]) -> dict:
    """명세가 없는 클립의 초안. 시리즈는 원본의 것, 템플릿은 그 시리즈의 것(없으면 첫 템플릿)."""
    series = None
    if clip["source_series_id"] is not None:
        series = conn.execute(
            "select id, template_id from series where id = %s", (clip["source_series_id"],)
        ).fetchone()
    usable = [t for t in _template_rows(conn) if t["error"] is None]
    if not usable:
        raise HTTPException(409, "쓸 수 있는 템플릿이 없습니다")
    template = next((t for t in usable if series and t["id"] == series["template_id"]), usable[0])
    return layouts.default(
        template["spec"], template_id=template["id"], series_id=series["id"] if series else None,
        title=clip["title"], channel=clip["channel"], src_w=src[0], src_h=src[1],
    )


def _parts(conn: psycopg.Connection, clip_id: int) -> list[dict]:
    return rows(
        conn, "select ordinal, start_sec, end_sec from clip_parts where clip_id = %s order by ordinal", (clip_id,)
    )


def _ranges(clip: dict, parts: list[dict]) -> list[tuple[float, float]]:
    """클립이 원본에서 차지하는 구간들 — 렌더(render.render_card)와 같은 규칙이다(조각이 둘 이상일 때만 조각)."""
    if len(parts) > 1:
        return [(float(p["start_sec"]), float(p["end_sec"])) for p in parts]
    return [(float(clip["start_sec"]), float(clip["end_sec"]))]


def _view(conn: psycopg.Connection, clip: dict, resolved: dict, saved: bool, error: str | None = None) -> dict:
    """편집 화면 한 벌 — 클립 · 원본 · 명세 · 자리 · 목록 · 그려 둔 글자 층."""
    parts = _parts(conn, clip["id"])
    series = resolved["series"]
    cached = card.lookup(
        deps.cfg, resolved["spec"], resolved["layout"], series["name"] if series else None, resolved["badge_file"]
    )
    snapshot = clip["render_spec"]
    # 🔴 "다시 렌더해야 한다" 의 근거. 명세가 같아도 템플릿을 고쳤으면 결과가 다르다 — 둘 다 본다.
    stale = saved and (
        not clip["rendered"]
        or snapshot is None
        or snapshot.get("layout") != resolved["layout"]
        or (snapshot.get("template") or {}).get("spec") != resolved["spec"]
        # 배지는 이미지(경로)든 글자(이름)든 바뀌면 결과가 다르다.
        or {k: (snapshot.get("series") or {}).get(k) for k in ("name", "badge_path")}
        != {k: (series or {}).get(k) for k in ("name", "badge_path")}
    )
    return {
        "clip": {
            "id": clip["id"], "title": clip["title"], "start_sec": clip["start_sec"], "end_sec": clip["end_sec"],
            "total_sec": clip["total_sec"], "rendered": clip["rendered"],
            # 장면 시작 초가 놓이는 시간의 길이(조각 합, 브릿지 제외).
            "duration": render.clip_duration(clip),
            # 조합 클립의 조각들. 화면의 재생기가 이 순서로 돈다(조각 하나뿐이면 start~end).
            "parts": parts if len(parts) > 1 else [],
            "renderedAs": ("card" if snapshot else "blur") if clip["rendered"] else None,
            "renderedAt": snapshot.get("rendered_at") if snapshot else None,
        },
        "source": {
            "id": clip["source_id"], "width": resolved["src_w"], "height": resolved["src_h"],
            "channel": clip["channel"], "seriesId": clip["source_series_id"],
            "videoUrl": f"/api/clips/{clip['id']}/source",
        },
        "layout": resolved["layout"],
        "saved": saved,
        "stale": stale,
        "error": error,
        "geometry": resolved["geometry"],
        "cardUrl": f"/api/cards/{cached.name}" if cached else None,
        "templates": _template_rows(conn),
        "series": _series_rows(conn),
    }


# ---------------------------------------------------------------- 템플릿 · 시리즈

@router.get("/templates")
def list_templates() -> list[dict]:
    with connect() as conn:
        return _template_rows(conn)


@router.get("/series")
def list_series() -> list[dict]:
    with connect() as conn:
        return _series_rows(conn)


def _check_template(conn: psycopg.Connection, template_id: int | None) -> None:
    if template_id is not None and conn.execute(
        "select 1 from templates where id = %s", (template_id,)
    ).fetchone() is None:
        raise HTTPException(422, "없는 템플릿입니다")


@router.post("/series")
def create_series(body: SeriesIn) -> dict:
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(422, "시리즈 이름을 입력하세요")
    with connect() as conn:
        _check_template(conn, body.templateId)
        try:
            row = conn.execute(
                "insert into series (name, template_id) values (%s, %s) returning id",
                (name, body.templateId),
            ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(409, f"'{name}' 시리즈가 이미 있습니다") from exc
        conn.commit()
        return next(s for s in _series_rows(conn) if s["id"] == row["id"])


@router.patch("/series/{series_id}")
def patch_series(series_id: int, body: SeriesPatchIn) -> dict:
    with connect() as conn:
        if conn.execute("select 1 from series where id = %s", (series_id,)).fetchone() is None:
            raise HTTPException(404, "series not found")
        if body.name is not None:
            name = " ".join(body.name.split())
            if not name:
                raise HTTPException(422, "시리즈 이름을 입력하세요")
            try:
                conn.execute("update series set name = %s where id = %s", (name, series_id))
            except psycopg.errors.UniqueViolation as exc:
                raise HTTPException(409, f"'{name}' 시리즈가 이미 있습니다") from exc
        if body.clearTemplate:
            conn.execute("update series set template_id = null where id = %s", (series_id,))
        elif body.templateId is not None:
            _check_template(conn, body.templateId)
            conn.execute("update series set template_id = %s where id = %s", (body.templateId, series_id))
        conn.commit()
        return next(s for s in _series_rows(conn) if s["id"] == series_id)


def _badge_type(data: bytes, content_type: str) -> str:
    """올린 파일의 확장자. 🔴 헤더만 믿지 않고 첫 바이트를 본다 — 이름만 .png 인 다른 파일을 ffmpeg 에 넣지 않는다."""
    ext = BADGE_TYPES.get(content_type.split(";")[0].strip().lower())
    sniffed = (
        "png" if data.startswith(b"\x89PNG\r\n\x1a\n")
        else "jpg" if data.startswith(b"\xff\xd8\xff")
        else "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        else None
    )
    if ext is None or sniffed != ext:
        raise HTTPException(415, "배지는 PNG · JPEG · WebP 이미지만 올릴 수 있습니다")
    return ext


@router.put("/series/{series_id}/badge")
def put_badge(series_id: int, request: Request, data: bytes = Body(..., media_type="application/octet-stream")) -> dict:
    """배지 이미지(본문이 이미지 바이트 그대로). P3 전까지는 원티드 숏폼 스크린샷에서 오려 올린다(tease §5-10)."""
    if not data:
        raise HTTPException(422, "빈 파일입니다")
    if len(data) > BADGE_MAX_BYTES:
        raise HTTPException(413, "배지 이미지는 2MB 까지입니다")
    ext = _badge_type(data, request.headers.get("content-type", ""))
    with connect() as conn:
        row = conn.execute("select badge_path from series where id = %s for update", (series_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "series not found")
        out_dir = deps.cfg.work_dir / BADGE_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        # 이름에 내용 해시를 넣는다 — 같은 시리즈의 배지를 바꾸면 이름이 바뀌어 미리보기 캐시와 브라우저 캐시가 함께 풀린다.
        out = out_dir / f"series{series_id}-{hashlib.sha256(data).hexdigest()[:12]}.{ext}"
        out.write_bytes(data)
        conn.execute("update series set badge_path = %s where id = %s", (deps.cfg.store_work(out), series_id))
        conn.commit()
        _remove_old_badge(row["badge_path"], out)
        return next(s for s in _series_rows(conn) if s["id"] == series_id)


@router.delete("/series/{series_id}/badge")
def delete_badge(series_id: int) -> dict:
    """배지 이미지를 뺀다 — 그 뒤로는 시리즈 이름이 글자 배지로 그려진다."""
    with connect() as conn:
        row = conn.execute("select badge_path from series where id = %s for update", (series_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "series not found")
        conn.execute("update series set badge_path = null where id = %s", (series_id,))
        conn.commit()
        _remove_old_badge(row["badge_path"], None)
        return next(s for s in _series_rows(conn) if s["id"] == series_id)


def _remove_old_badge(stored: str | None, keep: Any) -> None:
    """예전 배지 파일을 지운다. 🔴 badges 폴더 안의 것만 — DB 값이 엉뚱한 경로를 가리켜도 그 파일을 지우지 않는다.

    🔴 지난 렌더의 render_spec 이 그 경로를 들고 있지만 영상은 이미 구워졌다. 다시 렌더하면 지금 배지로 그린다.
    """
    if not stored:
        return
    old = deps.cfg.work_file(stored)
    if old.parent == deps.cfg.work_dir / BADGE_DIR and old != keep:
        old.unlink(missing_ok=True)


@router.get("/series/{series_id}/badge")
def get_badge(series_id: int) -> Any:
    with connect() as conn:
        row = conn.execute("select badge_path from series where id = %s", (series_id,)).fetchone()
    if row is None or not row["badge_path"]:
        raise HTTPException(404, "badge not found")
    path = deps.cfg.work_file(row["badge_path"])
    if not path.is_file():
        raise HTTPException(404, "badge file missing")
    return FileResponse(path)


# ---------------------------------------------------------------- 클립 편집 명세

@router.get("/clips/{clip_id}/layout")
def get_layout(clip_id: int) -> dict:
    """편집 화면을 여는 데 필요한 전부. 명세가 없으면 초안을 준다(`saved: false`) — 저장하기 전에는 DB 를 바꾸지 않는다."""
    with connect() as conn:
        clip = _clip(conn, clip_id)
        src = _source_size(clip)
        if clip["layout"] is not None:
            try:
                resolved = render.resolve(
                    conn, deps.cfg, clip["source_path"], clip["layout"], render.clip_duration(clip)
                )
                return _view(conn, clip, resolved, saved=True)
            except render.RenderError as exc:
                # 저장된 명세가 지금은 풀리지 않는다(템플릿이 지워졌다 등). 화면이 열리지 않으면 고칠 길도 없다 —
                # 초안을 보여 주고 이유를 함께 싣는다.
                error = str(exc)
        else:
            error = None
        resolved = _resolve(conn, clip, _default(conn, clip, src))
        return _view(conn, clip, resolved, saved=False, error=error)


@router.put("/clips/{clip_id}/layout")
def put_layout(clip_id: int, body: LayoutIn) -> dict:
    """명세를 저장한다. 렌더는 하지 않는다 — 렌더는 기존 `POST /clips/{id}/render`(잡)가 이 명세를 읽는다."""
    with connect() as conn:
        clip = _clip(conn, clip_id)
        _source_size(clip)
        resolved = _resolve(conn, clip, body.layout)
        conn.execute("update clips set layout = %s where id = %s", (Jsonb(resolved["layout"]), clip_id))
        series_id = resolved["layout"]["series_id"]
        if series_id is not None and clip["source_series_id"] is None:
            # 이 영상에서 처음 고른 시리즈가 이 영상의 기본이 된다 — 다음 클립을 열면 그 시리즈 · 템플릿으로 시작한다.
            conn.execute("update sources set series_id = %s where id = %s", (series_id, clip["source_id"]))
        conn.commit()
        return _view(conn, _clip(conn, clip_id), resolved, saved=True)


@router.delete("/clips/{clip_id}/layout")
def delete_layout(clip_id: int) -> dict:
    """명세를 지운다 — 다음 렌더는 예전의 블러 렌더다. 이미 구운 영상은 다시 렌더할 때까지 그대로다."""
    with connect() as conn:
        _clip(conn, clip_id)
        conn.execute("update clips set layout = null where id = %s", (clip_id,))
        conn.commit()
    return {"layout": None}


@router.post("/clips/{clip_id}/layout/preview")
def preview_layout(clip_id: int, body: LayoutIn) -> dict:
    """저장하지 않은 명세로 글자 층을 그린다. 정규화한 명세 · 자리 · PNG 주소를 함께 돌려준다.

    🔴 화면은 칸의 위치를 이 응답의 `geometry` 로 그린다 — PNG 의 글자 자리와 같은 계산에서 나온 값이다.
    """
    with connect() as conn:
        clip = _clip(conn, clip_id)
        _source_size(clip)
        resolved = _resolve(conn, clip, body.layout)
        spec = resolved["spec"]
        if ffmpeg.font_available(spec["font"]) is False:
            raise HTTPException(
                422, f"템플릿 글꼴 '{spec['font']}' 가 서버에 없습니다 — 그대로 그리면 글자가 네모로 찍힙니다"
            )
        series = resolved["series"]
        try:
            png = card.timed_png(
                conn, deps.cfg, clip["source_id"], clip_id, spec, resolved["layout"],
                series["name"] if series else None, resolved["badge_file"],
            )
        except ffmpeg.FfmpegError as exc:
            raise HTTPException(500, f"미리보기를 그리지 못했습니다: {exc}") from exc
    return {"layout": resolved["layout"], "geometry": resolved["geometry"], "cardUrl": f"/api/cards/{png.name}"}


@router.post("/clips/{clip_id}/layout/scenes")
def split_scenes(clip_id: int, body: LayoutIn) -> dict:
    """클립을 장면으로 나눠 **보낸 명세의 창**을 장면마다 하나로 다시 나눈다. 저장하지 않는다 — 화면이 초안에 넣는다.

    장면 경계는 ffmpeg 장면 점수로 잡는다(`pipeline/scenes.py`, LLM 0회). 새 장면의 상자는 그 장면 가운데를 덮던
    옛 상자를 물려받는다 — 사람이 고친 상자를 잃지 않는다(`layout.split`).

    🔴 ffmpeg 를 돌리는 행동이라 POST 다 — 보기 전용은 메서드로 403 이 난다. 편집 화면을 여는 GET 에서 돌리지 않는
    이유도 같다.
    """
    with connect() as conn:
        clip = _clip(conn, clip_id)
        _source_size(clip)
        current = _resolve(conn, clip, body.layout)
        try:
            found = scenes.detect(
                conn, deps.cfg, source_id=clip["source_id"], clip_id=clip_id, source_path=clip["source_path"],
                ranges=_ranges(clip, _parts(conn, clip_id)),
            )
        except ffmpeg.FfmpegError as exc:
            raise HTTPException(500, f"장면을 나누지 못했습니다: {exc}") from exc
        windows = layouts.split(current["layout"]["windows"], found["starts"], found["duration"])
        resolved = _resolve(conn, clip, current["layout"] | {"windows": windows})
    return {"layout": resolved["layout"], "geometry": resolved["geometry"], "scenes": found}


@router.post("/clips/{clip_id}/layout/focus")
def focus_scenes(clip_id: int, body: LayoutIn) -> dict:
    """[AI 초점] — 보낸 명세의 장면마다 사람을 찾아 창을 맞춘다(`pipeline/focus.py`). 저장하지 않는다.

    사람이 잡은 창(by=person)은 건드리지 않고 모델에도 보내지 않는다. Gemini 1회(장면마다 프레임 세 장), 클립 4 에서
    17초 · 약 20원. 실패하면 502 — 창은 그대로이고 화면이 이유를 보인다.

    🔴 유료 호출이라 POST 다 — 보기 전용은 메서드로 403 이 난다. 편집 화면을 여는 GET 에서 부르지 않는다.
    """
    with connect() as conn:
        clip = _clip(conn, clip_id)
        _source_size(clip)
        current = _resolve(conn, clip, body.layout)
        try:
            found = focus.run(
                conn, deps.cfg, source_id=clip["source_id"], clip_id=clip_id, source_path=clip["source_path"],
                ranges=_ranges(clip, _parts(conn, clip_id)), layout=current["layout"],
                src_w=current["src_w"], src_h=current["src_h"],
            )
        except ffmpeg.FfmpegError as exc:
            raise HTTPException(500, f"프레임을 뽑지 못했습니다: {exc}") from exc
        except focus.FocusError as exc:
            raise HTTPException(502, str(exc)) from exc
        resolved = _resolve(conn, clip, current["layout"] | {"windows": found["windows"]})
    return {
        "layout": resolved["layout"], "geometry": resolved["geometry"],
        "focus": {"scenes": found["scenes"], "called": found["called"], "latencyMs": found["latencyMs"]},
    }


@router.get("/cards/{name}")
def get_card(name: str) -> Any:
    """그려 둔 글자 층. 이름이 내용의 해시라 내용이 바뀌면 이름이 바뀐다 — 오래 캐시해도 된다."""
    path = card.card_file(deps.cfg, name)
    if path is None:
        raise HTTPException(404, "card not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/clips/{clip_id}/source")
def get_clip_source(clip_id: int) -> Any:
    """편집 화면이 영역 상자를 얹어 볼 원본. 🔴 원본 파일을 그대로 준다 — 브라우저가 range 요청으로 클립 구간만 읽는다.

    잘라 두지 않는 이유: 상자를 고치려면 원본 해상도 그대로의 전체 화면이 필요하고, 자르면 키프레임 스냅으로 시작이
    몇 초 어긋난다(ffmpeg.copy_segment). 재생기는 클립의 start~end 만 돈다.
    """
    with connect() as conn:
        clip = _clip(conn, clip_id)
    path = deps.cfg.source_file(clip["source_path"])
    if not path.is_file():
        raise HTTPException(404, "source file missing")
    return FileResponse(path, media_type="video/mp4" if path.suffix.lower() == ".mp4" else None)
