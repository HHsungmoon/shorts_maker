"""자막 번인 (문서 §9-9).

9:16 으로 리프레이밍하면 위아래 블러 영역이 완전히 빈다. 강연은 말이 콘텐츠 전부라
그 자리를 자막이 채워야 한다 — A7 결과를 눈으로 보고 내린 결론이다.

🔴 **발화를 그대로 자막으로 쓰지 않는다.** 발화는 평균 4초, 최대 11초라 한 장에 다 넣으면
글자가 너무 많고 화면이 오래 멈춘다. word-level 타임스탬프로 다시 쪼갠다 — 이게 §4-[1] 에서
word_timestamps 를 켜둔 실제 쓸모다.
"""

import json
from dataclasses import dataclass
from pathlib import Path

# 1080 폭에서 폰트 58px 이면 한 줄에 한글 16자쯤 들어간다. 두 줄까지 허용해 32자.
MAX_CHARS = 32
LINE_CHARS = 16
MAX_SEC = 4.0
MIN_SEC = 0.8

# 16:9 원본을 1080 폭으로 맞추면 높이 608, 1920 프레임 중앙에 놓이면 656~1264 를 차지한다.
# 그 아래 블러 영역이 자막 자리다.
DEFAULT_MARGIN_V = 1300
DEFAULT_FONT = "Apple SD Gothic Neo"
DEFAULT_FONT_SIZE = 58


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _words_of(utterance: dict) -> list[dict]:
    raw = utterance.get("words")
    if not raw:
        # word 정보가 없으면 발화 하나를 통째로 한 덩어리로 본다(자막이 아예 없는 것보다 낫다).
        return [{"start": utterance["start_sec"], "end": utterance["end_sec"], "text": utterance["text"]}]
    return json.loads(raw) if isinstance(raw, str) else raw


def build_cues(
    utterances: list[dict],
    clip_start: float,
    clip_end: float,
    max_chars: int = MAX_CHARS,
    max_sec: float = MAX_SEC,
) -> list[Cue]:
    """클립 구간의 발화를 자막 단위로 쪼갠다. 시각은 **클립 시작 기준 상대 초**다.

    ffmpeg 가 `-ss` 로 잘라낸 뒤의 타임라인에 얹히므로 절대 초를 그대로 쓰면 전부 어긋난다.
    """
    cues: list[Cue] = []
    buffer: list[dict] = []

    def flush() -> None:
        if not buffer:
            return
        text = "".join(w["text"] for w in buffer).strip()
        start = max(buffer[0]["start"], clip_start) - clip_start
        end = min(buffer[-1]["end"], clip_end) - clip_start
        buffer.clear()
        if not text or end <= start:
            return
        # 너무 짧게 스치는 자막은 읽히지 않는다. 다음 자막을 밀지 않는 선에서 살짝 늘린다.
        if end - start < MIN_SEC:
            end = min(start + MIN_SEC, clip_end - clip_start)
        cues.append(Cue(round(start, 3), round(end, 3), text))

    for utterance in utterances:
        if utterance["end_sec"] <= clip_start or utterance["start_sec"] >= clip_end:
            continue
        for word in _words_of(utterance):
            if word["end"] <= clip_start or word["start"] >= clip_end:
                continue
            pending = "".join(w["text"] for w in buffer) + word["text"]
            too_long = len(pending.strip()) > max_chars
            too_slow = buffer and (word["end"] - buffer[0]["start"]) > max_sec
            if buffer and (too_long or too_slow):
                flush()
            buffer.append(word)
        flush()
    return cues


def wrap(text: str, line_chars: int = LINE_CHARS) -> str:
    """두 줄까지 균형 있게 접는다. ASS 의 줄바꿈 마커는 `\\N` 이다.

    🔴 **escape 다음에 부른다.** 순서를 바꾸면 escape 가 이 마커의 백슬래시까지 이스케이프해
    화면에 `\\N` 이 글자로 찍힌다.
    """
    if len(text) <= line_chars:
        return text
    target = len(text) // 2
    # 가운데에서 가장 가까운 띄어쓰기를 찾는다 — 단어 중간에서 접으면 읽기 나쁘다.
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return text[:target] + "\\N" + text[target:]
    cut = min(spaces, key=lambda i: abs(i - target))
    return text[:cut] + "\\N" + text[cut + 1 :]


def escape(text: str) -> str:
    # ASS 에서 `{` 는 오버라이드 블록의 시작이라 그대로 두면 뒤 텍스트가 통째로 사라진다.
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def to_ass(
    cues: list[Cue],
    font: str = DEFAULT_FONT,
    font_size: int = DEFAULT_FONT_SIZE,
    margin_v: int = DEFAULT_MARGIN_V,
) -> str:
    # Alignment 8 = 상단 중앙. MarginV 는 위쪽 여백이라, 영상 아래 블러 영역에서 시작해
    # 아래로 자란다 — 두 줄이 돼도 영상을 가리지 않는다.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,4,2,8,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""
    lines = [
        f"Dialogue: 0,{timestamp(c.start)},{timestamp(c.end)},Default,,0,0,,{wrap(escape(c.text))}"
        for c in cues
    ]
    return header + "\n".join(lines) + "\n"


def write_ass(path: Path, cues: list[Cue], **style) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_ass(cues, **style), encoding="utf-8")
    return path


def build_part_cues(
    part_utterances: list[list[dict]],
    parts: list[tuple[float, float]],
    bridge_sec: float = 0.4,
    bridge_text: str = "{position}에서 이어집니다",
) -> list[Cue]:
    """조각들을 이어붙인 **하나의 타임라인** 기준 자막을 만든다.

    조각을 따로 렌더하지 않고 한 번에 이어붙이므로(ffmpeg.render_parts), 자막 시각도 이어붙인
    뒤 기준이어야 한다. 두 번째 조각의 자막은 첫 조각 길이 + 브릿지 길이만큼 뒤로 밀린다.

    🔴 브릿지 카드의 글자도 **같은 자막 파일**에 넣는다. 점프를 숨기지 않고 읽히게 만드는 쪽이
    낫다 — 시청자는 "편집됐다" 를 알고 봐야 한다(tease §5-6). 별도 필터를 쓰지 않아 필터 그래프도
    단순해진다.
    """
    if len(part_utterances) != len(parts):
        raise ValueError(f"조각 수가 안 맞는다: 발화 {len(part_utterances)}, 범위 {len(parts)}")
    cues: list[Cue] = []
    offset = 0.0
    for index, (utterances, (start, end)) in enumerate(zip(part_utterances, parts)):
        for cue in build_cues(utterances, start, end):
            cues.append(Cue(round(cue.start + offset, 3), round(cue.end + offset, 3), cue.text))
        offset += end - start
        if index < len(parts) - 1:
            # 다음 조각이 원본의 어디인지 알려준다. 분만 쓰면 같은 분 안의 점프가 "0분에서"가 되어
            # 이상하다 — 영상 위치는 사람들이 늘 mm:ss 로 말한다.
            at = parts[index + 1][0]
            cues.append(
                Cue(round(offset, 3), round(offset + bridge_sec, 3),
                    bridge_text.format(position=f"{int(at // 60)}:{int(at % 60):02d}"))
            )
            offset += bridge_sec
    return cues
