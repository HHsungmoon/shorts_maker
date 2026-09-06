"""A7 — [7] 리프레이밍 + 렌더 (문서 §4-[7], §9-9)."""

import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import ffmpeg
from . import subtitles


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

    conn.execute("update clips set path = %s, rendered = true where id = %s", (cfg.store_work(out), clip_id))
    conn.execute(
        "insert into stage_calls (source_id, run_id, stage, latency_ms, params) values (%s, %s, 'render', %s, %s)",
        (
            clip["source_id"],
            clip["run_id"],
            latency_ms,
            Jsonb(
                {
                    "clip_id": clip_id,
                    "duration_sec": round(clip["end_sec"] - clip["start_sec"], 2),
                    "subtitles": bool(subtitle_path),
                    "cues": cue_count,
                }
            ),
        ),
    )
    conn.commit()
    return out
