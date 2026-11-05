"""클립 편집 명세(`clips.layout`) — 검증 · 기본값 · 창 계산(tease §5-10). 순수 함수만 있다.

명세 한 벌:

    {
      "version": 1,
      "template_id": 2, "series_id": 2,
      "headline": ["삼성 출신 면접관이", "뽑는 답변은 이것"],
      "credit": "출처 : 채널 원티드",
      "subtitles": true, "subtitles_by": "auto",
      "slot_height": 853,
      "windows": [{"start": 0, "end": null, "x": 0.16, "y": 0.0, "w": 0.67, "by": "person"}]
    }

- **창(window)** 은 원본에서 칸에 보여 줄 영역이다. x · y · w 는 원본 폭 · 높이에 대한 **비율**이다(해상도가 바뀌어도
  같은 자리를 가리킨다). 높이는 적지 않는다 — 칸의 비율(1080 : slot_height)에서 나온다. 그래서 칸 높이를 바꾸면
  창의 높이가 함께 바뀌고, 원본 밖으로 넘치면 가운데를 지킨 채 폭을 줄인다(`fit_window`).
- **by** 는 누가 정했나. `auto`(코드 기본값) · `ai`(P5) · `person`. 🔴 `person` 인 값은 AI 를 다시 돌려도 덮지 않는다.
- 창은 지금 한 개(클립 전체)다. P5 에서 샷마다 늘어난다 — 그래서 처음부터 목록이고 start/end 를 둔다.

🔴 **칸 높이 ≤ 1080 이고 창 폭이 원본 1080px 이상이면 확대가 없다**(1080p 원본). 창을 좁게 잡을수록 확대가
커져 흐려진다 — `geometry` 가 배율과 경고를 돌려주고 편집 화면이 그대로 보여 준다.
"""

from . import templates

LAYOUT_VERSION = 1
BY_VALUES = ("auto", "ai", "person")
HEADLINE_MAX = 40  # 이보다 긴 줄은 화면 폭 두 배를 넘는다 — 오타나 붙여넣기 사고로 본다
CREDIT_MAX = 60
# 배율 상한. 3배면 1080p 원본에서 폭 360px 을 1080 으로 늘린 것이다 — 그보다 좁은 창은 알아볼 수 없을 만큼 뭉갠다.
MAX_UPSCALE = 3.0
# 이 배율을 넘으면 "흐려진다" 고 알린다. 1.2배까지는 폰에서 차이를 거의 못 느꼈다(설계안 v3 §06 의 기준).
SOFT_UPSCALE = 1.2
# 글자 폭 추정의 여백. 1080 에서 좌우 60 씩 남긴다.
TEXT_WIDTH = templates.FRAME_W - 120


class LayoutError(ValueError):
    pass


def _even(value: float) -> int:
    # 🔴 yuv420p 는 가로 · 세로가 짝수여야 한다. 칸 높이와 자른 크기를 짝수로 맞춘다.
    return max(2, int(round(value / 2)) * 2)


def text_width(text: str, size: int) -> float:
    """글자 폭 추정(px). 한글 0.92em · 공백 0.28 · 영숫자 0.62 · 그 밖 0.42 — 고딕 굵은체에서 잰 값의 근사다.

    정확하지 않아도 된다. 넘칠 것 같다고 **알리는** 데만 쓰고, 실제 모양은 미리보기 PNG(같은 libass)가 보여 준다.
    """
    width = 0.0
    for ch in text:
        if "가" <= ch <= "힣":
            width += 0.92
        elif ch == " ":
            width += 0.28
        elif ch.isascii() and ch.isalnum():
            width += 0.62
        else:
            width += 0.42
    return width * size


def split_headline(title: str | None, spec: dict) -> list[str]:
    """제목을 카피 두 줄의 초안으로 접는다. 넘치는 뒷부분은 버린다 — 초안이고 사람이 고친다(P4 부터 AI 가 낸다)."""
    block = templates.block_of(spec, "headline")
    count = len(block["lines"]) if block else 2
    lines = [""] * count
    if not block or not title:
        return lines
    words = " ".join(title.split()).split(" ")
    index = 0
    for word in words:
        candidate = f"{lines[index]} {word}".strip()
        if text_width(candidate, block["size"]) <= TEXT_WIDTH or not lines[index]:
            lines[index] = candidate
            continue
        index += 1
        if index >= count:
            break
        lines[index] = word
    return [line[:HEADLINE_MAX] for line in lines]


def window_height(window: dict, slot_height: int, src_w: int, src_h: int) -> float:
    """창의 높이(원본 높이에 대한 비율). 창 폭(px) × 칸 비율 ÷ 원본 높이."""
    return window["w"] * src_w * slot_height / templates.FRAME_W / src_h


def fit_window(
    slot_height: int, src_w: int, src_h: int, window: dict | None = None
) -> dict:
    """칸 비율에 맞는 창. window 가 있으면 그 가운데와 폭을 지키고, 원본 밖으로 넘치는 만큼만 줄이고 민다.

    window 가 없으면 들어가는 가장 큰 창을 가운데에 둔다 — 16:9 원본 · 칸 608 이면 원본 전체다.
    """
    ratio = slot_height / templates.FRAME_W  # 창 높이(px) / 창 폭(px)
    max_w = min(1.0, src_h / (src_w * ratio))  # 높이가 원본을 넘지 않는 가장 큰 폭(비율)
    if window is None:
        w = max_w
        cx, cy = 0.5, 0.5
        by = "auto"
    else:
        w = min(float(window["w"]), max_w)
        old_h = window_height(window, slot_height, src_w, src_h)
        cx = float(window["x"]) + float(window["w"]) / 2
        cy = float(window["y"]) + min(old_h, 1.0) / 2
        by = window.get("by", "auto")
    h = w * src_w * ratio / src_h
    # 🔴 바깥 max 가 마지막이다 — h 가 부동소수로 1.0000001 이 되면 1 - h 가 음수라 y 가 -0.0 으로 저장됐다.
    x = max(min(cx - w / 2, 1.0 - w), 0.0)
    y = max(min(cy - h / 2, 1.0 - h), 0.0)
    out = {
        "start": 0,
        "end": None,
        "x": round(x, 5),
        "y": round(y, 5),
        "w": round(w, 5),
        "by": by,
    }
    if window is not None:
        out["start"] = window.get("start", 0)
        out["end"] = window.get("end")
    return out


def default(
    spec: dict, *, template_id: int, series_id: int | None, title: str | None,
    channel: str | None, src_w: int, src_h: int,
) -> dict:
    """클립에 명세가 없을 때의 초안. 칸은 템플릿 기본 높이, 창은 그 칸에 들어가는 가장 큰 가운데 영역이다."""
    low, high = templates.slot_limits(spec)
    slot = templates.block_of(spec, "slot")
    slot_height = _even(min(max(slot["default"], low), high))
    return {
        "version": LAYOUT_VERSION,
        "template_id": template_id,
        "series_id": series_id,
        "headline": split_headline(title, spec),
        "credit": templates.credit_text(spec, channel),
        # 🔴 P1 의 기본값은 켜기다. 원본에 자막이 박혀 있는지는 P5 에서 AI 가 보고 정한다 — 그 전에는 사람이 끈다.
        "subtitles": True,
        "subtitles_by": "auto",
        "slot_height": slot_height,
        "windows": [fit_window(slot_height, src_w, src_h)],
    }


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayoutError(f"{where} 는 숫자여야 한다 ({value!r})")
    return float(value)


def validate(raw: object, spec: dict, src_w: int, src_h: int) -> dict:
    """화면이 보낸 명세를 검사하고 정규화한 사본을 돌려준다.

    🔴 창은 **조금 넘친 것은 고치고 말이 안 되는 것은 거절한다.** 화면의 끌기는 부동소수 오차로 원본 밖으로
    0.001 쯤 나가기 일쑤다 — 그걸 422 로 돌려보내면 끌 때마다 저장이 실패한다. 반대로 폭 0 이나 음수는 화면의
    버그이니 고쳐 주지 않고 알린다.
    """
    if not isinstance(raw, dict):
        raise LayoutError("명세는 객체여야 한다")

    headline = raw.get("headline")
    block = templates.block_of(spec, "headline")
    count = len(block["lines"]) if block else 0
    if not isinstance(headline, list) or any(not isinstance(line, str) for line in headline):
        raise LayoutError("카피는 문자열 목록이어야 한다")
    lines = [" ".join(line.split()) for line in headline][:count] if count else []
    lines += [""] * (count - len(lines))
    for index, line in enumerate(lines):
        if len(line) > HEADLINE_MAX:
            raise LayoutError(f"카피 {index + 1}줄이 {HEADLINE_MAX}자를 넘는다")

    credit = raw.get("credit") or ""
    if not isinstance(credit, str):
        raise LayoutError("출처는 문자열이어야 한다")
    credit = " ".join(credit.split())
    if len(credit) > CREDIT_MAX:
        raise LayoutError(f"출처가 {CREDIT_MAX}자를 넘는다")

    subtitles = raw.get("subtitles", True)
    if not isinstance(subtitles, bool):
        raise LayoutError("자막 켜기는 참/거짓이어야 한다")

    slot_height = _number(raw.get("slot_height"), "칸 높이")
    low, high = templates.slot_limits(spec)
    if not low <= slot_height <= high:
        raise LayoutError(f"칸 높이는 이 템플릿에서 {low}~{high} 다 ({int(slot_height)})")
    slot_height = min(_even(slot_height), high - high % 2)

    windows = raw.get("windows")
    if not isinstance(windows, list) or len(windows) != 1:
        # TODO: P5 — 샷마다 창. 그때 start/end 가 겹치지 않고 클립을 빈틈없이 덮는지 검사한다.
        raise LayoutError("창은 지금 하나만 둔다(샷마다 창은 P5)")
    window = windows[0]
    if not isinstance(window, dict):
        raise LayoutError("창은 객체여야 한다")
    w = _number(window.get("w"), "창 폭")
    x = _number(window.get("x"), "창 x")
    y = _number(window.get("y"), "창 y")
    if not 0 < w <= 1.01 or not -0.01 <= x <= 1 or not -0.01 <= y <= 1:
        raise LayoutError("창이 원본 밖에 있다")
    by = window.get("by", "auto")
    if by not in BY_VALUES:
        raise LayoutError(f"창의 by 는 {', '.join(BY_VALUES)} 중 하나다")
    fitted = fit_window(int(slot_height), src_w, src_h, {"x": x, "y": y, "w": min(w, 1.0), "by": by})
    if templates.FRAME_W / (fitted["w"] * src_w) > MAX_UPSCALE:
        raise LayoutError(f"창이 너무 좁다 — {MAX_UPSCALE:g}배 넘게 키우면 알아볼 수 없다")

    subtitles_by = raw.get("subtitles_by", "auto")
    if subtitles_by not in BY_VALUES:
        raise LayoutError(f"subtitles_by 는 {', '.join(BY_VALUES)} 중 하나다")

    return {
        "version": LAYOUT_VERSION,
        "template_id": raw.get("template_id"),
        "series_id": raw.get("series_id"),
        "headline": lines,
        "credit": credit,
        "subtitles": subtitles,
        "subtitles_by": subtitles_by,
        "slot_height": int(slot_height),
        "windows": [fitted],
    }


def crop_px(window: dict, slot_height: int, src_w: int, src_h: int) -> dict:
    """ffmpeg crop 인자(px). 폭 · 높이는 짝수이고 원본 안에 들어간다."""
    cw = min(_even(window["w"] * src_w), src_w - src_w % 2)
    ch = min(_even(cw * slot_height / templates.FRAME_W), src_h - src_h % 2)
    cx = min(max(int(round(window["x"] * src_w)), 0), src_w - cw)
    cy = min(max(int(round(window["y"] * src_h)), 0), src_h - ch)
    return {"w": cw, "h": ch, "x": cx, "y": cy}


def geometry(spec: dict, layout: dict, src_w: int, src_h: int) -> dict:
    """편집 화면과 렌더가 함께 쓰는 자리 계산 — 블록 위치 · 칸 범위 · 자를 영역 · 배율 · 경고.

    🔴 화면은 이 값을 그대로 그린다(칸 위치를 따로 계산하지 않는다). 계산이 두 곳에 있으면 미리보기의 영상 칸과
    렌더 결과의 칸이 어긋나도 아무도 모른다.
    """
    slot_height = layout["slot_height"]
    boxes = templates.stack(spec, slot_height)
    low, high = templates.slot_limits(spec)
    crop = crop_px(layout["windows"][0], slot_height, src_w, src_h)
    upscale = round(templates.FRAME_W / crop["w"], 2)
    warnings: list[str] = []
    if upscale > SOFT_UPSCALE:
        warnings.append(f"원본을 {upscale:g}배 키운다 — 흐려 보일 수 있다")
    headline = templates.block_of(spec, "headline")
    if headline:
        for index, line in enumerate(layout["headline"]):
            if line and text_width(line, headline["size"]) > TEXT_WIDTH:
                warnings.append(f"카피 {index + 1}줄이 화면 폭을 넘을 수 있다 — 미리보기로 확인한다")
    if templates.block_of(spec, "badge") and layout.get("series_id") is None:
        warnings.append("시리즈를 고르지 않아 배지 자리가 비어 있다")
    return {
        "frame": {"w": templates.FRAME_W, "h": templates.FRAME_H},
        "safe": {"top": templates.SAFE_TOP, "bottom": templates.SAFE_BOTTOM},
        "boxes": {kind: {"top": box.top, "height": box.height} for kind, box in boxes.items()},
        "slotLimits": [low, high],
        "crop": crop,
        "upscale": upscale,
        "warnings": warnings,
    }
