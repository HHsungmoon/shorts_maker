"""장면마다 사람에 맞춘 창 — AI 초점(tease §5-10 "포커스 규칙", 2026-11-07).

장면마다 프레임 세 장을 Gemini 에 한 번에 보내 **사람 상자 · 머리 상자 · 말하는 사람**만 받는다. 창 좌표는 여기 규칙이
계산한다 — LLM 이 창을 직접 내면 "좌우 여백을 같게" 같은 규칙을 지키는지 검사할 길이 없다.

    셋 이상    모두 담는다. 사람들 양 끝에서 원본 폭 MARGIN 씩 띄운다
    둘         말하는 사람 한 명에 맞춘다. 못 가리면 둘 다 담는다
    한 명      그 사람에 맞춘다. 폭은 그 사람 폭의 ONE_SCALE 배
    없음       창을 그대로 둔다(자료 화면 · 풍경)

어느 쪽이든 창의 **가운데를 사람(들)의 가운데**에 둔다 — 좌우 여백이 같아진다. 원본 가운데에 두면 사람이 한쪽에 앉은
장면이 늘 틀어진다(클립 4 클로즈업: 사람 가운데 x 0.585). 머리 위는 창 높이의 HEADROOM.

숫자의 근거 — 사람이 클립 4 를 직접 맞춘 상자(2026-11-07 스크린샷에서 잰 값):

    장면 5(3명)   x 0.157 · w 0.66 · y 0.07   사람들은 x 0.185~0.780 → 양 끝 여백 0.03 안팎
    장면 4(2명)   x 0.45  · w 0.54 · y 0.01   말하는 사람 x 0.610~0.903(폭 0.293) → 창 폭은 그 1.8배

말하는 사람: 프레임 세 장(장면의 20 · 50 · 80%)에서 입 모양 · 손짓 · 다른 사람의 시선으로 가린다. 상자는 가운데(B)에서 잰다.
클립 4 실측(gemini-3.6-flash, 저해상도): 여섯 장면의 상자 모두 맞음 · 장면 4 의 말하는 사람(오른쪽) 맞음 · 17초 · 입력
5천 토큰 · 사고 1.8천 토큰 · 약 20원.

🔴 사람이 잡은 창(by=person)은 **입력에서 뺀다** — 보내고 결과를 버리는 것보다 싸고, 덮어쓸 길이 아예 없다.
🔴 시각은 우리가 정한다(프레임을 뽑은 자리). LLM 은 이 단계에서 초를 말하지 않는다.
"""

import json
import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import ffmpeg, gemini
from . import templates
from .layout import SOFT_UPSCALE

# 장면 안에서 프레임을 뽑는 자리(몫). 상자는 가운데(B)에서 잰다. 셋인 이유는 말하는 사람 — 한 장으로는 입이 열린
# 순간인지 말하는 중인지 모른다.
FRAMES = (("A", 0.2), ("B", 0.5), ("C", 0.8))
# 보내는 프레임 폭. 저해상도(gemini.generate_json)로 보내므로 이보다 키워도 모델이 보는 것은 같다.
FRAME_WIDTH = 768
# 여럿을 담을 때 사람들 양 끝 바깥 여백(원본 폭 비율).
MARGIN = 0.03
# 한 명에 맞출 때 창 폭 = 그 사람 상자 폭 × 이 값. 클로즈업은 상한(원본 높이 전체)에 걸려 그대로 남는다.
ONE_SCALE = 1.8
# 맨 위 머리 위로 비우는 몫(창 높이 비율). 사람이 잡은 두 상자(0.15 · 0.10)의 가운데.
HEADROOM = 0.12
# 장면 수 상한 — 프레임 수가 곧 비용이다. layout.MAX_WINDOWS(60) 장면이면 180장인데, 그건 화면의 버그다.
MAX_SCENES = 24

PROMPT = """You see frames from a talk-show video, grouped by scene. Each scene is one continuous camera shot.
For every scene there are three frames A, B, C taken at 20%, 50% and 80% of the scene.

For each scene:
- people: every person visible in frame B. Ignore people cut off so that less than half of their head is visible.
  - box_2d: [ymin, xmin, ymax, xmax] of the whole visible person (head, body, arms, legs), normalized 0-1000 on frame B
  - head_2d: [ymin, xmin, ymax, xmax] of the head including hair, normalized 0-1000 on frame B
- speaker: index into people of the person who is talking during this scene, judged from mouth movement across A, B, C,
  gestures and where the others are looking. -1 if you cannot tell.
"""

_BOX = {"type": "array", "items": {"type": "integer"}}
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer"},
                    "people": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"box_2d": _BOX, "head_2d": _BOX},
                            "required": ["box_2d", "head_2d"],
                        },
                    },
                    "speaker": {"type": "integer"},
                },
                "required": ["scene", "people", "speaker"],
            },
        }
    },
    "required": ["scenes"],
}


class FocusError(RuntimeError):
    pass


def run(
    conn: psycopg.Connection, cfg: config.Config, *, source_id: int, clip_id: int, source_path: str,
    ranges: list[tuple[float, float]], layout: dict, src_w: int, src_h: int,
) -> dict:
    """검증한 명세(layout)의 창 가운데 사람이 잡지 않은 것을 사람에 맞춘다. 저장하지 않는다.

    돌려주는 것: windows(새 창 목록, 맞춘 창은 by=ai) · scenes(장면마다 어떻게 맞췄나) · called(모델을 불렀나) ·
    latencyMs. 실패하면 FocusError(기록하고 올린다) — 창은 그대로 두고 화면이 이유를 보인다.
    """
    windows = [dict(w) for w in layout["windows"]]
    duration = sum(end - start for start, end in ranges)
    targets = [index for index, window in enumerate(windows) if window.get("by") != "person"]
    summary = [{"scene": index + 1, "how": "person", "people": None} for index in range(len(windows))]
    if not targets:
        return {"windows": windows, "scenes": summary, "called": False, "latencyMs": 0}
    if len(targets) > MAX_SCENES:
        raise FocusError(f"장면이 {MAX_SCENES}개를 넘는다({len(targets)}) — 장면을 합친 뒤 다시 누른다")

    source = str(cfg.source_file(source_path))
    started = time.monotonic()
    images: list[tuple[str, bytes]] = []
    for number, index in enumerate(targets, start=1):
        start = windows[index]["start"]
        end = windows[index + 1]["start"] if index + 1 < len(windows) else duration
        for label, share in FRAMES:
            at = source_time(ranges, start + (end - start) * share)
            images.append((f"Scene {number}, frame {label}", ffmpeg.frame_jpeg(source, at, FRAME_WIDTH, cfg.ffmpeg_bin)))
    frames_ms = int((time.monotonic() - started) * 1000)
    # 🔴 모델 호출이 십수 초다 — 그동안 트랜잭션을 붙잡지 않는다.
    conn.commit()

    params = {"clip_id": clip_id, "scenes": len(targets), "frames": len(images), "frame_width": FRAME_WIDTH,
              "frames_ms": frames_ms}
    found: list[dict | None] | None = None
    # 🔴 응답 전체가 깨졌을 때만 한 번 더 부른다(LLM 출력 규약). 장면 하나가 이상한 것은 그 장면만 건너뛴다 — 다시
    # 부르면 17초와 20원이 다시 든다.
    for attempt in (1, 2):
        try:
            raw, usage, latency_ms = gemini.generate_json(cfg, PROMPT, RESPONSE_SCHEMA, images)
        except Exception as exc:
            _record(conn, source_id, None, None, params | {"attempt": attempt}, f"{type(exc).__name__}: {exc}"[-500:], cfg)
            conn.commit()
            raise FocusError(f"AI 가 답하지 못했다: {exc}") from exc
        call_id = _record(conn, source_id, usage, latency_ms, params | {"attempt": attempt}, None, cfg, raw)
        conn.commit()
        try:
            found = parse(raw, len(targets))
            break
        except FocusError as exc:
            conn.execute("update stage_calls set error = %s where id = %s", (str(exc), call_id))
            conn.commit()
            if attempt == 2:
                raise

    if found is None:  # 위 반복이 break 없이 끝나는 길은 없다 — 타입 검사기를 위한 줄
        raise FocusError("AI 응답을 읽지 못했다")
    slot_height = layout["slot_height"]
    for number, index in enumerate(targets):
        scene = found[number]
        if scene is None:
            summary[index] = {"scene": index + 1, "how": "missing", "people": None}
            continue
        box, how = frame(scene["people"], scene["speaker"], slot_height, src_w, src_h)
        summary[index] = {"scene": index + 1, "how": how, "people": len(scene["people"])}
        if box is not None:
            windows[index] = {**windows[index], **box, "by": "ai"}
    conn.execute(
        "update stage_calls set params = params || %s where id = %s",
        (Jsonb({"framed": [s for s in summary if s["how"] != "person"]}), call_id),
    )
    conn.commit()
    return {"windows": windows, "scenes": summary, "called": True,
            "latencyMs": int((time.monotonic() - started) * 1000)}


def source_time(ranges: list[tuple[float, float]], moment: float) -> float:
    """클립 안의 moment 초 → 원본의 초. 조각을 이어 붙인 시간이다(브릿지 없음 — 편집 화면의 재생 막대와 같다)."""
    offset = 0.0
    for start, end in ranges:
        if moment < offset + (end - start):
            return start + max(0.0, moment - offset)
        offset += end - start
    start, end = ranges[-1]
    return max(start, end - 0.05)


def _box(value: object) -> dict | None:
    """모델의 [ymin, xmin, ymax, xmax](0~1000) → {x0, y0, x1, y1}(0~1). 말이 안 되면 None.

    살짝 넘친 것(-10~1010)은 잘라 쓴다 — 화면 끝에 걸친 사람을 1001 로 적는 일이 있다.
    """
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not -10 <= v <= 1010 for v in value):
        return None
    y0, x0, y1, x1 = (min(max(float(v), 0.0), 1000.0) / 1000 for v in value)
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def parse(raw: str, count: int) -> list[dict | None]:
    """응답 → 장면마다 {people: [{box, head}], speaker: 번호 | None}. 응답에 없거나 망가진 장면은 None.

    - 장면 번호는 1~count 만 받는다(화이트리스트). 같은 번호가 또 오면 앞의 것을 쓴다
    - 상자가 말이 안 되는 사람은 뺀다. 머리 상자만 망가졌으면 사람 상자 위쪽을 머리로 본다
    - 말하는 사람은 **남은 사람** 안의 번호로 옮긴다 — 뺀 사람을 가리키면 None
    응답 전체가 JSON 이 아니거나 모양이 틀리면 FocusError.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FocusError(f"AI 응답이 JSON 이 아니다: {exc}") from exc
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(scenes, list):
        raise FocusError("AI 응답에 scenes 가 없다")
    out: list[dict | None] = [None] * count
    for item in scenes:
        if not isinstance(item, dict):
            continue
        number = item.get("scene")
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= count:
            continue
        if out[number - 1] is not None:
            continue
        people: list[dict] = []
        kept: dict[int, int] = {}
        for position, person in enumerate(item.get("people") or []):
            if not isinstance(person, dict):
                continue
            box = _box(person.get("box_2d"))
            if box is None:
                continue
            head = _box(person.get("head_2d")) or {**box, "y1": box["y0"] + (box["y1"] - box["y0"]) * 0.25}
            kept[position] = len(people)
            people.append({"box": box, "head": head})
        speaker = item.get("speaker")
        speaker = kept.get(speaker) if isinstance(speaker, int) and not isinstance(speaker, bool) else None
        out[number - 1] = {"people": people, "speaker": speaker}
    return out


def frame(
    people: list[dict], speaker: int | None, slot_height: int, src_w: int, src_h: int
) -> tuple[dict | None, str]:
    """사람 상자들 → 창 {x, y, w}(원본 비율) 과 어떻게 맞췄나(group · speaker · one · none). 사람이 없으면 창 None.

    창의 폭은 칸 비율로 높이가 원본을 넘지 않는 가장 큰 폭과, SOFT_UPSCALE 배까지만 키우는 가장 작은 폭 사이다.
    가운데를 사람(들)의 가운데에 두고, 원본 밖으로 나가는 만큼만 민다 — 사람이 원본 끝에 앉아 있으면 그쪽 여백은 못 맞춘다.
    """
    if not people:
        return None, "none"
    if len(people) == 1:
        subject, how = people, "one"
    elif len(people) == 2 and speaker is not None:
        subject, how = [people[speaker]], "speaker"
    else:
        subject, how = people, "group"

    ratio = slot_height / templates.FRAME_W
    max_w = min(1.0, src_h / (src_w * ratio))
    min_w = min(max_w, templates.FRAME_W / (src_w * SOFT_UPSCALE))
    if len(subject) == 1:
        person = subject[0]
        box, head = person["box"], person["head"]
        width = (box["x1"] - box["x0"]) * ONE_SCALE
        # 몸의 가운데와 얼굴의 가운데를 반씩 — 옆으로 앉은 사람은 둘이 다르다. 얼굴만 보면 몸이 한쪽으로 쏠리고,
        # 몸만 보면 손짓한 팔이 가운데를 끌고 간다.
        center = ((box["x0"] + box["x1"]) / 2 + (head["x0"] + head["x1"]) / 2) / 2
    else:
        left = min(p["box"]["x0"] for p in subject)
        right = max(p["box"]["x1"] for p in subject)
        width = right - left + 2 * MARGIN
        center = (left + right) / 2
    w = min(max(width, min_w), max_w)
    h = w * src_w * ratio / src_h
    top = min(p["head"]["y0"] for p in subject)
    x = min(max(center - w / 2, 0.0), 1.0 - w)
    y = min(max(top - HEADROOM * h, 0.0), max(1.0 - h, 0.0))
    return {"x": round(x, 5), "y": round(y, 5), "w": round(w, 5)}, how


def _record(
    conn: psycopg.Connection, source_id: int, usage: dict | None, latency_ms: int | None, params: dict,
    error: str | None, cfg: config.Config, raw: str | None = None,
) -> int:
    usage = usage or {}
    return conn.execute(
        """insert into stage_calls
           (source_id, stage, model, input_tokens, output_tokens, thinking_tokens, total_tokens, cached_tokens,
            latency_ms, prompt, response, params, error)
           values (%s, 'focus', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (
            source_id, cfg.gemini_model, usage.get("input_tokens"), usage.get("output_tokens"),
            usage.get("thinking_tokens"), usage.get("total_tokens"), usage.get("cached_tokens"), latency_ms,
            # 프롬프트 글만 남긴다. 프레임은 원본에서 언제든 다시 뽑을 수 있다(params 에 장면 수 · 폭).
            PROMPT if cfg.store_prompts else None, raw if cfg.store_prompts else None,
            Jsonb(params), error,
        ),
    ).fetchone()["id"]
