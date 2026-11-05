"""숏폼 템플릿 — 검증과 배치(tease §5-10). 순수 함수만 있다.

템플릿은 블록(배지 · 카피 · 영상 칸 · 출처)을 위에서 아래로 쌓는 규칙이다. 덩어리 전체의 가운데를 `anchor_y` 에
맞춘다 — 칸이 길어지면 머리는 위로, 출처는 아래로 함께 벌어진다. 원티드 숏폼 두 편을 1080×1920 에서 잰 값
(왓츠인 마이 AI 칸 614–1222, 잡소리 칸 504–1357)이 이 규칙 하나로 10px 안팎에 들어온다(설계안 v3 §01).

🔴 **위 120 · 아래 1480 밖에는 글자를 두지 않는다.** 위는 재생 버튼, 아래는 유튜브 제목 · 채널 · 설명이 덮는
자리다. 그래서 칸 높이의 상한은 템플릿마다 계산된다(`slot_limits`) — 배지를 끄면 칸을 더 키울 수 있다.
"""

from dataclasses import dataclass

FRAME_W = 1080
FRAME_H = 1920
SAFE_TOP = 120
SAFE_BOTTOM = 1480

BLOCK_TYPES = ("badge", "headline", "slot", "credit")
# 칸 높이 하한. 16:9 원본을 폭 1080 에 맞춘 높이(608)보다 낮으면 원본 위아래를 자르는 쪽이 된다 — 그럴 이유가 없다.
SLOT_FLOOR = 608
# 칸 높이 상한의 상한. 칸이 1080 을 넘으면 1080p 원본의 세로(1080)보다 길어 반드시 확대된다.
SLOT_CEILING = 1080


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Box:
    """1080×1920 프레임 안의 사각형. y 는 위에서부터."""

    top: int
    height: int

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def middle(self) -> int:
        return self.top + self.height // 2


def _color(value: object, where: str) -> str:
    text = str(value or "")
    if len(text) != 7 or text[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in text[1:]):
        raise TemplateError(f"{where}: 색은 #RRGGBB 로 적는다 ({value!r})")
    return text.upper()


def _int(block: dict, key: str, low: int, high: int, default: int | None = None) -> int:
    value = block.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise TemplateError(f"{block.get('type')}.{key}: {low}~{high} 사이 정수여야 한다 ({value!r})")
    return value


def validate(spec: object) -> dict:
    """템플릿 spec 을 검사하고 빠진 기본값을 채운 사본을 돌려준다.

    🔴 spec 은 jsonb 라 DB 가 모양을 지키지 못한다. 렌더 직전이 아니라 **저장할 때** 여기서 막는다 — 렌더에서
    터지면 몇 분 기다린 뒤에 안다.
    """
    if not isinstance(spec, dict):
        raise TemplateError("spec 은 객체여야 한다")
    blocks = spec.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise TemplateError("blocks 가 비어 있다")
    types = [b.get("type") if isinstance(b, dict) else None for b in blocks]
    for kind in types:
        if kind not in BLOCK_TYPES:
            raise TemplateError(f"모르는 블록: {kind!r}")
    for kind in BLOCK_TYPES:
        if types.count(kind) > 1:
            raise TemplateError(f"{kind} 블록이 둘이다")
    if "slot" not in types:
        raise TemplateError("영상 칸(slot) 블록이 없다")

    out_blocks: list[dict] = []
    for block in blocks:
        kind = block["type"]
        clean: dict = {"type": kind, "gap": _int(block, "gap", 0, 300, 0)}
        if kind == "badge":
            clean |= {
                "height": _int(block, "height", 30, 300),
                "size": _int(block, "size", 16, 120, 46),
                "fill": _color(block.get("fill", "#111111"), "badge.fill"),
                "color": _color(block.get("color", "#FFFFFF"), "badge.color"),
            }
        elif kind == "headline":
            lines = block.get("lines")
            if not isinstance(lines, list) or not 1 <= len(lines) <= 2:
                raise TemplateError("headline.lines 는 한 줄 또는 두 줄이다")
            clean |= {
                "size": _int(block, "size", 30, 140),
                "line_height": _int(block, "line_height", 30, 200),
                "line_gap": _int(block, "line_gap", 0, 100, 0),
                "max_chars": _int(block, "max_chars", 4, 40),
                "lines": [
                    {"color": _color(line.get("color"), f"headline.lines[{i}].color")}
                    | ({"band": _color(line["band"], f"headline.lines[{i}].band")} if line.get("band") else {})
                    for i, line in enumerate(lines)
                    if isinstance(line, dict)
                ],
            }
            if len(clean["lines"]) != len(lines):
                raise TemplateError("headline.lines 의 줄은 객체여야 한다")
        elif kind == "slot":
            clean |= {
                "min": _int(block, "min", SLOT_FLOOR, SLOT_CEILING),
                "max": _int(block, "max", SLOT_FLOOR, SLOT_CEILING),
                "default": _int(block, "default", SLOT_FLOOR, SLOT_CEILING),
                "subtitle_size": _int(block, "subtitle_size", 24, 100, 52),
            }
            if not clean["min"] <= clean["default"] <= clean["max"]:
                raise TemplateError("slot: min ≤ default ≤ max 여야 한다")
        elif kind == "credit":
            text = str(block.get("text") or "").strip()
            if not text:
                raise TemplateError("credit.text 가 비어 있다")
            clean |= {
                "height": _int(block, "height", 20, 200),
                "size": _int(block, "size", 16, 100),
                "color": _color(block.get("color", "#333333"), "credit.color"),
                "text": text,
            }
        out_blocks.append(clean)
    # 마지막 블록 뒤의 여백은 의미가 없다 — 덩어리 높이에 넣으면 가운데가 그만큼 어긋난다.
    out_blocks[-1]["gap"] = 0

    out = {
        "background": _color(spec.get("background", "#FFFFFF"), "background"),
        "anchor_y": spec.get("anchor_y", 790),
        "font": str(spec.get("font") or "NanumGothic").strip(),
        "blocks": out_blocks,
    }
    if not isinstance(out["anchor_y"], int) or not SAFE_TOP < out["anchor_y"] < SAFE_BOTTOM:
        raise TemplateError(f"anchor_y 는 {SAFE_TOP}~{SAFE_BOTTOM} 사이여야 한다")

    low, high = slot_limits(out)
    slot = block_of(out, "slot")
    if high < low or slot["min"] > high:
        raise TemplateError(
            f"글자가 안전 영역({SAFE_TOP}~{SAFE_BOTTOM})을 넘는다 — 칸을 {slot['min']} 로 잡을 자리가 없다"
        )
    return out


def block_of(spec: dict, kind: str) -> dict | None:
    return next((b for b in spec["blocks"] if b["type"] == kind), None)


def _height(block: dict, slot_height: int) -> int:
    if block["type"] == "slot":
        return slot_height
    if block["type"] == "headline":
        lines = len(block["lines"])
        return lines * block["line_height"] + (lines - 1) * block["line_gap"]
    return block["height"]


def stack(spec: dict, slot_height: int) -> dict[str, Box]:
    """블록마다 차지하는 자리. 덩어리의 가운데가 anchor_y 다.

    잡소리 칸 853 이면 덩어리 높이 440+853, 위 = 790 − 1293/2 = 143 → 배지 143–223(잰 값 150–230),
    칸 493–1346(잰 값 504–1357).
    """
    heights = [_height(b, slot_height) for b in spec["blocks"]]
    total = sum(heights) + sum(b["gap"] for b in spec["blocks"])
    y = spec["anchor_y"] - total // 2
    boxes: dict[str, Box] = {}
    for block, height in zip(spec["blocks"], heights):
        boxes[block["type"]] = Box(y, height)
        y += height + block["gap"]
    return boxes


def slot_limits(spec: dict) -> tuple[int, int]:
    """이 템플릿에서 칸 높이가 가질 수 있는 범위 (하한, 상한).

    상한은 템플릿에 적힌 max 와 "글자 블록이 안전 영역 안에 남는 가장 큰 칸" 중 작은 쪽이다. 칸이 2 커지면
    덩어리 위끝이 1 올라가므로 이분 탐색할 것도 없이 하나씩 보면 된다 — 범위가 수백이다.
    🔴 **짝수만** 본다. 칸 높이는 짝수로 저장되므로(yuv420p) 홀수 상한(901)을 주면 화면의 막대 끝과 저장값이 어긋난다.
    """
    slot = block_of(spec, "slot")
    low = slot["min"] + slot["min"] % 2
    best = SLOT_FLOOR - 2
    for height in range(SLOT_FLOOR, SLOT_CEILING + 1, 2):
        if _text_inside(spec, height):
            best = height
    return low, min(slot["max"] - slot["max"] % 2, best)


def _text_inside(spec: dict, slot_height: int) -> bool:
    boxes = stack(spec, slot_height)
    texts = [box for kind, box in boxes.items() if kind != "slot"]
    # 칸은 안전 영역을 넘어도 된다 — 영상이 UI 밑으로 조금 들어가는 것은 원티드 숏폼도 그렇다. 글자만 지킨다.
    return all(box.top >= SAFE_TOP and box.bottom <= SAFE_BOTTOM for box in texts)


def credit_text(spec: dict, channel: str | None) -> str:
    """출처 문구의 기본값. 채널 이름이 없으면 빈 문자열 — 사람이 채운다."""
    block = block_of(spec, "credit")
    if block is None or not channel:
        return ""
    return block["text"].replace("{channel}", channel.strip())
