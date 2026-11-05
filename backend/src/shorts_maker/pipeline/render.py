"""A7 — [7] 리프레이밍 + 렌더 (문서 §4-[7], §9-9)."""

import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import ffmpeg
from . import card, layout as layouts, subtitles, templates


class RenderError(RuntimeError):
    pass


def load_clip(conn: psycopg.Connection, clip_id: int) -> dict:
    clip = conn.execute(
        """select cl.*, sg.chunk_id, s.id as source_id, s.path as source_path
           from clips cl
           join segments sg on sg.id = cl.segment_id
           join chunks ch on ch.id = sg.chunk_id
           join sources s on s.id = ch.source_id
           where cl.id = %s""",
        (clip_id,),
    ).fetchone()
    if clip is None:
        raise RenderError(f"clip {clip_id} 없음")
    return clip


def build_subtitle_file(
    conn: psycopg.Connection, cfg: config.Config, clip: dict, out_dir: Path
) -> tuple[Path | None, int]:
    rows = conn.execute(
        """select idx, start_sec, end_sec, text, words from utterances
           where chunk_id = %s and end_sec > %s and start_sec < %s order by idx""",
        (clip["chunk_id"], clip["start_sec"], clip["end_sec"]),
    ).fetchall()
    cues = subtitles.build_cues(
        [dict(r) for r in rows], float(clip["start_sec"]), float(clip["end_sec"])
    )
    if not cues:
        return None, 0
    path = subtitles.write_ass(
        out_dir / f"clip{clip['id']:03d}.ass", cues, font=cfg.subtitle_font
    )
    return path, len(cues)


def load_parts(conn: psycopg.Connection, clip_id: int) -> list[dict]:
    """클립의 조각들. 답하기 경로가 만든 클립은 1~3개, 기존 경로가 만든 것은 0개다."""
    rows = conn.execute(
        """select p.*, sg.chunk_id from clip_parts p
           join segments sg on sg.id = p.segment_id
           where p.clip_id = %s order by p.ordinal""",
        (clip_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def part_utterances(conn: psycopg.Connection, part: dict) -> list[dict]:
    rows = conn.execute(
        """select idx, start_sec, end_sec, text, words from utterances
           where chunk_id = %s and end_sec > %s and start_sec < %s order by idx""",
        (part["chunk_id"], part["start_sec"], part["end_sec"]),
    ).fetchall()
    return [dict(r) for r in rows]


def render_combined(
    conn: psycopg.Connection, cfg: config.Config, clip: dict, parts: list[dict],
    out: Path, out_dir: Path, burn_subtitles: bool,
) -> tuple[int, int]:
    """조각 여러 개를 이어붙여 렌더한다. (소요 ms, 자막 큐 수).

    🔴 자막은 이어붙인 타임라인 기준이다(subtitles.build_part_cues). 조각별 상대 초를 그대로
    쓰면 두 번째 조각부터 전부 어긋난다.
    """
    ranges = [(float(p["start_sec"]), float(p["end_sec"])) for p in parts]
    subtitle_path, cue_count = None, 0
    if burn_subtitles:
        cues = subtitles.build_part_cues([part_utterances(conn, p) for p in parts], ranges)
        if cues:
            subtitle_path = subtitles.write_ass(
                out_dir / f"clip{clip['id']:03d}.ass", cues, font=cfg.subtitle_font
            )
            cue_count = len(cues)
    conn.commit()
    started = time.monotonic()
    ffmpeg.render_parts(
        str(cfg.source_file(clip["source_path"])), str(out), ranges,
        subtitle_path=str(subtitle_path) if subtitle_path else None, binary=cfg.ffmpeg_bin,
    )
    return int((time.monotonic() - started) * 1000), cue_count


def run_for_clip(
    conn: psycopg.Connection, cfg: config.Config, clip_id: int, force: bool, burn_subtitles: bool = True
) -> Path:
    clip = load_clip(conn, clip_id)
    if clip["rendered"] and not force:
        raise RenderError(f"clip {clip_id} 은 이미 렌더됐다 — 다시 하려면 --force")

    source = cfg.source_file(clip["source_path"])
    if not source.is_file():
        raise RenderError(f"원본이 없다: {source}")

    out_dir = cfg.work_dir / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"clip{clip_id:03d}.mp4"

    # 조각이 여럿이면 이어붙인다. 하나뿐이면 아래의 기존 경로 그대로 — 단일 컷은 검증된 길로 간다.
    parts = load_parts(conn, clip_id)

    # 편집 명세가 있으면 카드로 만든다(tease §5-10). 없으면 아래의 블러 렌더 그대로다.
    if clip["layout"] is not None:
        return render_card(conn, cfg, clip, parts, source, out, out_dir, burn_subtitles)
    if len(parts) > 1:
        if burn_subtitles and not ffmpeg.has_filter("ass", cfg.ffmpeg_bin):
            raise RenderError(f"{cfg.ffmpeg_bin} 에 libass 가 없어 자막을 넣을 수 없다")
        latency_ms, cue_count = render_combined(
            conn, cfg, clip, parts, out, out_dir, burn_subtitles
        )
        return _finish(conn, cfg, clip, out, latency_ms, cue_count, burn_subtitles, len(parts))

    subtitle_path, cue_count = (None, 0)
    if burn_subtitles:
        # 🔴 자막을 켰는데 조용히 빠지면 안 된다 — 결과물만 봐서는 "자막이 원래 없는 클립"과
        # 구분되지 않는다. 필터가 없으면 여기서 멈추고 이유를 알려준다(§9-9).
        if not ffmpeg.has_filter("ass", cfg.ffmpeg_bin):
            raise RenderError(
                f"{cfg.ffmpeg_bin} 에 libass 가 없어 자막을 넣을 수 없다. "
                "SHORTS_FFMPEG 를 libass 포함 빌드로 지정하거나 --no-subtitles 로 끈다 "
                "(macOS: brew install ffmpeg-full → /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg)"
            )
        if ffmpeg.font_available(cfg.subtitle_font) is False:
            raise RenderError(
                f"자막 폰트 '{cfg.subtitle_font}' 를 찾을 수 없다. 이대로 렌더하면 글자가 아니라"
                " 네모(□)로 찍힌다 — SHORTS_SUBTITLE_FONT 를 설치된 폰트로 바꾸거나"
                " 한글 폰트를 설치한다(데비안: apt-get install fonts-nanum)"
            )
        subtitle_path, cue_count = build_subtitle_file(conn, cfg, clip, out_dir)

    # 🔴 인코딩은 분 단위다. 트랜잭션을 열어둔 채 몇 분 계산하지 않는다 — 위 읽기로 열린 트랜잭션을 여기서 끊는다.
    conn.commit()
    started = time.monotonic()
    ffmpeg.render_vertical(
        str(source),
        str(out),
        float(clip["start_sec"]),
        float(clip["end_sec"]),
        subtitle_path=str(subtitle_path) if subtitle_path else None,
        binary=cfg.ffmpeg_bin,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    return _finish(conn, cfg, clip, out, latency_ms, cue_count, bool(subtitle_path), 1)


def resolve(conn: psycopg.Connection, cfg: config.Config, source_path: str, raw_layout: dict) -> dict:
    """명세가 가리키는 템플릿 · 시리즈를 읽고 검증한다. 렌더와 편집 화면(http/layouts.py)이 같은 길을 쓴다.

    돌려주는 것: template(행) · spec(검증한 것) · series(행 또는 None) · badge_file · src_w/src_h · layout(정규화) · geometry.

    🔴 템플릿은 **지금 DB 에 있는 것**을 읽는다. 렌더가 끝나면 그 사본을 render_spec 에 남긴다 — 템플릿을 나중에
    고쳐도 이 영상이 무엇으로 만들어졌는지는 남는다.
    """
    template = conn.execute(
        "select id, name, spec from templates where id = %s", (raw_layout.get("template_id"),)
    ).fetchone()
    if template is None:
        raise RenderError("명세의 템플릿이 없다 — 지워졌으면 편집 화면에서 다시 고른다")
    try:
        spec = templates.validate(template["spec"])
    except templates.TemplateError as exc:
        raise RenderError(f"템플릿 '{template['name']}' 이 잘못됐다: {exc}") from exc
    series = None
    if raw_layout.get("series_id") is not None:
        series = conn.execute(
            "select id, name, badge_path from series where id = %s", (raw_layout["series_id"],)
        ).fetchone()
        if series is None:
            raise RenderError("명세의 시리즈가 없다 — 지워졌으면 편집 화면에서 다시 고른다")
    badge_file = None
    if series and series["badge_path"]:
        badge_file = cfg.work_file(series["badge_path"])
        if not badge_file.is_file():
            # 🔴 조용히 글자 배지로 바꾸지 않는다 — 결과만 봐서는 "이미지를 안 올렸다" 와 구분되지 않는다.
            raise RenderError(f"시리즈 '{series['name']}' 의 배지 파일이 없다: {series['badge_path']}")
    src_w, src_h = ffmpeg.video_size(str(cfg.source_file(source_path)))
    try:
        normalized = layouts.validate(raw_layout, spec, src_w, src_h)
    except layouts.LayoutError as exc:
        raise RenderError(f"편집 명세가 잘못됐다: {exc}") from exc
    return dict(
        template=dict(template), spec=spec, series=dict(series) if series else None, badge_file=badge_file,
        src_w=src_w, src_h=src_h, layout=normalized,
        geometry=layouts.geometry(spec, normalized, src_w, src_h),
    )


def card_subtitles(cues: list[subtitles.Cue], path: Path, spec: dict, geometry: dict) -> Path:
    """카드 렌더의 자막 파일. 칸 안 아래쪽(\an2) · 템플릿 글꼴 · 칸 블록의 자막 크기."""
    slot = geometry["boxes"]["slot"]
    size = templates.block_of(spec, "slot")["subtitle_size"]
    # 칸 아래끝에서 36px 위가 자막의 아래끝이다. 원티드 숏폼에 박힌 자막이 그쯤 앉는다(잡소리 스크린샷).
    margin_v = templates.FRAME_H - (slot["top"] + slot["height"]) + 36
    return subtitles.write_ass(path, cues, font=spec["font"], font_size=size, margin_v=margin_v, alignment=2)


def render_card(
    conn: psycopg.Connection, cfg: config.Config, clip: dict, parts: list[dict],
    source: Path, out: Path, out_dir: Path, burn_subtitles: bool,
) -> Path:
    """카드 숏폼 — 글자 층 PNG 위에 창만큼 자른 영상 · 칸 안의 자막.

    🔴 자막은 **명세의 켜기와 호출의 켜기가 둘 다 참**일 때만 넣는다. 명세가 끈 것을 호출이 켜지 않고, CLI 의
    `--no-subtitles` 는 명세가 켠 것도 끈다 — 어느 한쪽이 끈 것을 다른 쪽이 조용히 되살리지 않는다.
    """
    if not ffmpeg.has_filter("ass", cfg.ffmpeg_bin):
        # 카드는 글자 층부터 libass 로 그린다 — 자막을 꺼도 필요하다.
        raise RenderError(f"{cfg.ffmpeg_bin} 에 libass 가 없어 카드의 글자를 그릴 수 없다")
    resolved = resolve(conn, cfg, clip["source_path"], clip["layout"])
    spec, layout, geometry = resolved["spec"], resolved["layout"], resolved["geometry"]
    if ffmpeg.font_available(spec["font"]) is False:
        raise RenderError(
            f"템플릿 글꼴 '{spec['font']}' 를 찾을 수 없다. 이대로 그리면 글자가 네모(□)로 찍힌다"
            " — 템플릿의 font 를 설치된 글꼴로 바꾸거나 글꼴을 설치한다(데비안: apt-get install fonts-nanum)"
        )
    series = resolved["series"]
    png = card.timed_png(
        conn, cfg, clip["source_id"], clip["id"], spec, layout,
        series["name"] if series else None, resolved["badge_file"],
    )

    ranges = [(float(p["start_sec"]), float(p["end_sec"])) for p in parts] if len(parts) > 1 else [
        (float(clip["start_sec"]), float(clip["end_sec"]))
    ]
    subtitle_path, cue_count = None, 0
    if layout["subtitles"] and burn_subtitles:
        if len(parts) > 1:
            cues = subtitles.build_part_cues([part_utterances(conn, p) for p in parts], ranges)
        else:
            rows = conn.execute(
                """select idx, start_sec, end_sec, text, words from utterances
                   where chunk_id = %s and end_sec > %s and start_sec < %s order by idx""",
                (clip["chunk_id"], clip["start_sec"], clip["end_sec"]),
            ).fetchall()
            cues = subtitles.build_cues([dict(r) for r in rows], ranges[0][0], ranges[0][1])
        if cues:
            subtitle_path = card_subtitles(cues, out_dir / f"clip{clip['id']:03d}.ass", spec, geometry)
            cue_count = len(cues)

    slot = geometry["boxes"]["slot"]
    # 🔴 인코딩 앞에서 트랜잭션을 끊는다(위 읽기와 미리보기 기록).
    conn.commit()
    started = time.monotonic()
    ffmpeg.render_card(
        str(source), str(out), ranges, str(png.path), slot["top"], slot["height"], geometry["crop"],
        subtitle_path=str(subtitle_path) if subtitle_path else None, binary=cfg.ffmpeg_bin,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    snapshot = {
        "layout": layout,
        # 그때의 템플릿 사본. 템플릿을 고친 뒤에도 이 영상의 모양을 다시 만들 수 있다.
        "template": {"id": resolved["template"]["id"], "name": resolved["template"]["name"], "spec": spec},
        "series": {k: series[k] for k in ("id", "name", "badge_path")} if series else None,
        "source": {"width": resolved["src_w"], "height": resolved["src_h"]},
        "card": png.name,
        "subtitles": bool(subtitle_path),
        "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    conn.execute("update clips set render_spec = %s where id = %s", (Jsonb(snapshot), clip["id"]))
    return _finish(
        conn, cfg, clip, out, latency_ms, cue_count, bool(subtitle_path), max(len(parts), 1),
        extra={"card": True, "template_id": resolved["template"]["id"], "upscale": geometry["upscale"]},
    )


def _finish(
    conn: psycopg.Connection, cfg: config.Config, clip: dict, out: Path,
    latency_ms: int, cue_count: int, had_subtitles: bool, parts: int, extra: dict | None = None,
) -> Path:

    if not extra:
        # 블러 렌더로 다시 만들면 예전 카드 명세의 사본은 이 영상의 것이 아니다.
        conn.execute("update clips set render_spec = null where id = %s", (clip["id"],))
    conn.execute("update clips set path = %s, rendered = true where id = %s", (cfg.store_work(out), clip["id"]))
    conn.execute(
        "insert into stage_calls (source_id, run_id, stage, latency_ms, params) values (%s, %s, 'render', %s, %s)",
        (
            clip["source_id"],
            clip["run_id"],
            latency_ms,
            Jsonb(
                {
                    "clip_id": clip["id"],
                    # 🔴 조합 클립에서 start/end 는 **봉투**다(첫 조각 시작 ~ 마지막 조각 끝). 12:30 과
                    # 41:00 을 이은 30초짜리를 28분으로 기록하면 §7 의 시간 실측이 통째로 거짓이 된다.
                    "duration_sec": round(
                        float(clip["total_sec"]) if clip["total_sec"]
                        else clip["end_sec"] - clip["start_sec"], 2
                    ),
                    "subtitles": had_subtitles,
                    "cues": cue_count,
                    "parts": parts,
                    **(extra or {}),
                }
            ),
        ),
    )
    conn.commit()
    return out
