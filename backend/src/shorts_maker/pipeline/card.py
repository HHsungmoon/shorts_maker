"""카드의 글자 층 — 배지 · 카피 · 출처를 1080×1920 PNG 한 장으로 그린다(tease §5-10).

🔴 **미리보기와 렌더가 같은 PNG 를 쓴다.** 편집 화면은 이 PNG 위에 영상 칸만 브라우저에서 얹어 보여 주고, 렌더는
같은 PNG 를 바탕으로 깔고 영상을 칸에 얹는다. 글자를 브라우저(CSS)로 따로 그리면 글꼴 · 줄 높이 · 띠 크기가 렌더와
달라져 "미리보기에선 맞았는데" 가 생긴다. 그래서 글자는 libass 한 곳에서만 그린다.

그리는 법: 바탕색 한 장(lavfi color) → ASS(글자) → 배지 이미지가 있으면 그 위에 얹기 → PNG 한 장.
같은 입력이면 같은 파일이다 — 내용의 해시를 이름으로 써서 캐시한다(`work_dir/cards/`).
"""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import ffmpeg
from . import subtitles, templates

CARD_DIR = "cards"
# 화면이 GET 으로 PNG 를 읽을 때 이름을 검사한다(경로 조작 방지). 해시 앞 24자.
NAME_LENGTH = 24


@dataclass(frozen=True)
class Card:
    path: Path
    name: str
    made: bool  # 이번에 새로 그렸나(캐시 히트면 거짓 — stage_calls 에는 새로 그린 것만 남긴다)


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB → ASS 의 &HAABBGGRR. 🔴 순서가 거꾸로다(BGR). 그대로 넣으면 노랑이 하늘색으로 나온다."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _style(name: str, font: str, size: int, color: str, bold: bool, box: str | None, pad: int) -> str:
    # BorderStyle 3 = 글자를 감싸는 불투명 상자. 상자 색은 OutlineColour, 상자 여백은 Outline 값이다(libass).
    # 띠가 없는 줄은 BorderStyle 1 · 외곽선 0 이다 — 흰 바탕 위 검은 글자에 외곽선을 두면 번져 보인다.
    border_style, outline_colour, outline = (3, ass_color(box), pad) if box else (1, "&H00000000", 0)
    return (
        f"Style: {name},{font},{size},{ass_color(color)},&H000000FF,{outline_colour},&H00000000,"
        f"{-1 if bold else 0},0,0,0,100,100,0,0,{border_style},{outline},0,5,0,0,0,1"
    )


def _line(style: str, x: int, y: int, text: str, pad_sides: bool = False) -> str:
    body = subtitles.escape(text)
    if pad_sides:
        # 🔴 상자(BorderStyle 3)의 여백은 위아래와 좌우가 같다. 띠 높이에 맞춘 여백은 좌우로는 좁아서 글자가 띠
        # 끝에 붙는다 — 양쪽에 고정 공백(\h)을 둘 더 넣어 좌우만 넓힌다.
        body = "\\h\\h" + body + "\\h\\h"
    # \an5 = 가운데 기준점, \pos 로 블록 가운데에 박는다. 여백(MarginV)으로 두면 줄 수에 따라 자리가 움직인다.
    return f"Dialogue: 0,0:00:00.00,9:59:59.00,{style},,0,0,0,,{{\\an5\\pos({x},{y})}}{body}"


def build_ass(spec: dict, layout: dict, series_name: str | None, has_badge_image: bool) -> str:
    """글자 층의 ASS. 시각은 영상 전체(0 ~ 10시간) — 정지 화면 한 장을 뽑는 데 쓰므로 길이는 의미가 없다."""
    boxes = templates.stack(spec, layout["slot_height"])
    font = spec["font"]
    cx = templates.FRAME_W // 2
    styles: list[str] = []
    events: list[str] = []

    badge = templates.block_of(spec, "badge")
    if badge and series_name and not has_badge_image:
        # 이미지가 없으면 시리즈 이름을 글자 배지로 그린다(P3 전까지의 대안, tease §5-10).
        pad = max(4, (badge["height"] - int(badge["size"] * 1.2)) // 2)
        styles.append(_style("Badge", font, badge["size"], badge["color"], True, badge["fill"], pad))
        events.append(_line("Badge", cx, boxes["badge"].middle, series_name, pad_sides=True))

    headline = templates.block_of(spec, "headline")
    if headline:
        top = boxes["headline"].top
        for index, (line_spec, text) in enumerate(zip(headline["lines"], layout["headline"])):
            if not text:
                continue
            name = f"Headline{index}"
            band = line_spec.get("band")
            pad = max(2, (headline["line_height"] - int(headline["size"] * 1.2)) // 2)
            styles.append(_style(name, font, headline["size"], line_spec["color"], True, band, pad))
            middle = top + index * (headline["line_height"] + headline["line_gap"]) + headline["line_height"] // 2
            events.append(_line(name, cx, middle, text, pad_sides=bool(band)))

    credit = templates.block_of(spec, "credit")
    if credit and layout.get("credit"):
        styles.append(_style("Credit", font, credit["size"], credit["color"], False, None, 0))
        events.append(_line("Credit", cx, boxes["credit"].middle, layout["credit"]))

    header = (
        # 🔴 YCbCr Matrix: None — 없으면 libass 가 색을 TV 범위로 옮겨 #FFD400 이 #EBC610 으로 바랜다(ffmpeg.render_still).
        "[Script Info]\nScriptType: v4.00+\nYCbCr Matrix: None\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic,"
        " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL,"
        " MarginR, MarginV, Encoding\n"
    )
    # WrapStyle 2 = 줄을 접지 않는다. 카피 한 줄은 한 줄이다 — libass 가 멋대로 접으면 두 줄 블록이 세 줄이 된다.
    return header + "\n".join(styles) + "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + "\n".join(events) + "\n"


def badge_overlay(spec: dict, layout: dict, badge_file: Path | None) -> dict | None:
    """배지 이미지를 얹을 자리. 높이를 배지 블록에 맞추고 가로 가운데."""
    badge = templates.block_of(spec, "badge")
    if not badge or badge_file is None:
        return None
    box = templates.stack(spec, layout["slot_height"])["badge"]
    return {"path": str(badge_file), "top": box.top, "height": box.height}


def _plan(spec: dict, layout: dict, series_name: str | None, badge_file: Path | None) -> tuple[str, str, dict | None]:
    """(파일 이름 해시, ASS, 배지 자리). 같은 입력이면 같은 해시다 — ffmpeg 없이 캐시를 찾을 수 있다."""
    ass = build_ass(spec, layout, series_name, badge_file is not None)
    overlay = badge_overlay(spec, layout, badge_file)
    digest = hashlib.sha256(
        json.dumps(
            {
                "ass": ass,
                "background": spec["background"],
                "overlay": overlay,
                # 배지 파일 이름에 내용 해시가 들어 있지만(http/layouts.py), 손으로 바꿔 넣은 파일도 잡도록 크기·시각까지.
                "badge_stat": [badge_file.stat().st_size, badge_file.stat().st_mtime_ns] if badge_file else None,
            },
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:NAME_LENGTH]
    return digest, ass, overlay


def lookup(cfg: config.Config, spec: dict, layout: dict, series_name: str | None, badge_file: Path | None) -> Card | None:
    """이미 그려 둔 PNG 가 있으면 그것. 보기 전용 화면이 새로 그리지 않고 볼 수 있는 길이다."""
    digest, _, _ = _plan(spec, layout, series_name, badge_file)
    out = cfg.work_dir / CARD_DIR / f"{digest}.png"
    return Card(out, out.name, False) if out.is_file() else None


def ensure_png(
    cfg: config.Config, spec: dict, layout: dict, series_name: str | None, badge_file: Path | None
) -> Card:
    """글자 층 PNG 를 만든다(있으면 그대로 쓴다)."""
    digest, ass, overlay = _plan(spec, layout, series_name, badge_file)
    out_dir = cfg.work_dir / CARD_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{digest}.png"
    if out.is_file():
        return Card(out, out.name, False)
    ass_path = out_dir / f"{digest}.ass"
    ass_path.write_text(ass, encoding="utf-8")
    # 🔴 임시 이름에 쓰고 옮긴다. 미리보기 요청이 겹치면 반쯤 쓰인 PNG 를 다른 요청이 읽을 수 있다.
    partial = out_dir / f"{digest}.partial.png"
    try:
        ffmpeg.render_still(str(partial), str(ass_path), spec["background"], overlay, binary=cfg.ffmpeg_bin)
        partial.replace(out)
    finally:
        ass_path.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return Card(out, out.name, True)


def card_file(cfg: config.Config, name: str) -> Path | None:
    """화면이 고른 이름의 PNG. 🔴 이름은 해시 꼴만 받는다 — `../` 같은 것으로 work_dir 밖을 읽게 두지 않는다."""
    stem, dot, ext = name.partition(".")
    if ext != "png" or len(stem) != NAME_LENGTH or any(c not in "0123456789abcdef" for c in stem):
        return None
    path = cfg.work_dir / CARD_DIR / name
    return path if path.is_file() else None


def record_preview(conn: psycopg.Connection, source_id: int, clip_id: int, latency_ms: int, error: str | None) -> None:
    """새로 그린 미리보기를 단계로 남긴다. 구간 미리보기와 같은 'preview' 다 — 캐시 히트는 단계가 아니다."""
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms, params, error) values (%s, 'preview', %s, %s, %s)",
        (source_id, latency_ms, Jsonb({"kind": "card", "clip_id": clip_id}), error),
    )


def timed_png(
    conn: psycopg.Connection, cfg: config.Config, source_id: int, clip_id: int,
    spec: dict, layout: dict, series_name: str | None, badge_file: Path | None,
) -> Card:
    """ensure_png + 새로 그렸으면 stage_calls 기록."""
    started = time.monotonic()
    try:
        card = ensure_png(cfg, spec, layout, series_name, badge_file)
    except ffmpeg.FfmpegError as exc:
        record_preview(conn, source_id, clip_id, int((time.monotonic() - started) * 1000), str(exc)[-500:])
        conn.commit()
        raise
    if card.made:
        record_preview(conn, source_id, clip_id, int((time.monotonic() - started) * 1000), None)
        conn.commit()
    return card
