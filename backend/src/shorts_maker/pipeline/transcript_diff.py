"""자막 ↔ 음성 인식 비교 (tease §5-1, 2026-11-01). LLM 0회, 순수 계산.

음성 인식이 **들은 범위 안에서만** 맞댄다(자막은 영상 전체, 음성 인식은 들은 곳만 있다). 글자 단위로 맞추고
(공백·문장부호 빼고) 다른 곳을 **단어 단위로 넓혀** 사람이 읽을 수 있게 보여 준다.

어떻게 이렇게 됐나(make_shorts §7-1 재측정 2): 삼성 31분을 이 방식으로 맞대 보니 불일치 9.2% 중 대부분은
단어 하나를 다르게 들은 것이었고, 뜻을 바꾸는 것은 **음성 인식이 지어낸 문장** 몇 곳이었다. 그 문장의 단어는
소리에 붙일 곳이 없어 **시작 = 끝(0초)** 이 된다 — 그래서 그 표시로 가장 먼저 보여 준다.
"""

import difflib
import re
from dataclasses import asdict, dataclass

import psycopg

from . import transcripts

# 맞댈 때 빼는 글자. 띄어쓰기·문장부호는 두 쪽이 다르게 찍을 뿐 말이 다른 것이 아니다.
_IGNORE = re.compile(r"[\s.,?!·…\"'“”‘’\[\]()~\-%:;]")

# 다른 곳 앞뒤로 보여 줄 단어 수.
CONTEXT_WORDS = 3

# 종류 — 화면이 이 순서로 고르개를 늘어놓는다. 🔴 "invented" 가 맨 앞이다: 뜻을 바꾸는 것이 그것이다.
KINDS = ("invented", "caption_missing", "whisper_missing", "different", "number")


@dataclass
class Spot:
    start_sec: float
    end_sec: float
    # invented: 음성 인식 쪽에 0초 단어가 있다(지어냈을 수 있음) · caption_missing: 자막에 없는 말 ·
    # whisper_missing: 음성 인식에 없는 말 · different: 다르게 들음 · number: 숫자 표기만 다름("3개" · "세 개")
    kind: str
    caption: str
    whisper: str
    # 앞뒤 문맥(자막 쪽 단어. 자막 쪽이 비었으면 음성 인식 쪽).
    before: str
    after: str


def _words(rows: list[dict]) -> list[dict]:
    """발화 행들 → 단어 목록. 단어가 없는 행은 행 하나를 단어 하나로 둔다(시각을 지어내지 않는다)."""
    out: list[dict] = []
    for row in rows:
        words = row.get("words") or [{"start": row["start_sec"], "end": row["end_sec"], "text": " " + row["text"]}]
        out += [{"start": float(w["start"]), "end": float(w["end"]), "text": w["text"]} for w in words]
    return out


def _stream(words: list[dict]) -> tuple[str, list[int]]:
    """맞댈 글자열과, 글자마다 그 글자가 든 단어 번호."""
    chars: list[str] = []
    owner: list[int] = []
    for index, word in enumerate(words):
        for ch in _IGNORE.sub("", word["text"]):
            chars.append(ch)
            owner.append(index)
    return "".join(chars), owner


def _word_range(lo: int, hi: int, owner: list[int], n_words: int) -> tuple[int, int]:
    """글자 범위 [lo, hi) → 단어 범위 [a, b). 빈 글자 범위면 그 자리(단어 한가운데면 그 단어)."""
    if hi > lo:
        return owner[lo], owner[hi - 1] + 1
    if 0 < lo < len(owner) and owner[lo - 1] == owner[lo]:
        return owner[lo], owner[lo] + 1
    at = owner[lo] if lo < len(owner) else n_words
    return at, at


def _join(words: list[dict]) -> str:
    return re.sub(r"\s+", " ", "".join(w["text"] for w in words)).strip()


def _kind(caption: str, whisper: str, whisper_words: list[dict]) -> str:
    if whisper and any(w["end"] - w["start"] <= 1e-6 for w in whisper_words):
        return "invented"
    if not caption:
        return "caption_missing"
    if not whisper:
        return "whisper_missing"
    # 한쪽만 숫자를 썼다 — "3개" 와 "세 개". 말은 같다.
    if bool(re.search(r"\d", caption)) != bool(re.search(r"\d", whisper)):
        return "number"
    return "different"


def compare_window(caption_words: list[dict], whisper_words: list[dict]) -> tuple[list[Spot], int]:
    """한 범위 안의 두 단어 목록을 맞댄다. (다른 곳들, 다른 글자 수)."""
    c, c_owner = _stream(caption_words)
    w, w_owner = _stream(whisper_words)
    matcher = difflib.SequenceMatcher(None, c, w, autojunk=False)
    raw: list[list[int]] = []  # [자막 단어 a, b, 음성 인식 단어 a, b]
    mismatch = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        mismatch += max(i2 - i1, j2 - j1)
        ca, cb = _word_range(i1, i2, c_owner, len(caption_words))
        wa, wb = _word_range(j1, j2, w_owner, len(whisper_words))
        # **같은 단어에 걸친** 차이는 한 곳으로 — 한 단어 안의 두 글자 차이가 두 줄이 되면 읽기 어렵다.
        # 🔴 바로 옆 단어까지 묶지 않는다. "학보님들이 세" 처럼 따로 틀린 두 단어가 한 줄이 되면 종류가 흐려진다.
        if raw and (ca < raw[-1][1] or wa < raw[-1][3]):
            raw[-1][1] = max(raw[-1][1], cb)
            raw[-1][3] = max(raw[-1][3], wb)
        else:
            raw.append([ca, cb, wa, wb])

    spots: list[Spot] = []
    for ca, cb, wa, wb in raw:
        cws, wws = caption_words[ca:cb], whisper_words[wa:wb]
        caption, whisper = _join(cws), _join(wws)
        if _IGNORE.sub("", caption) == _IGNORE.sub("", whisper):
            continue
        involved = cws + wws
        if not involved:
            continue
        if caption_words:
            before = _join(caption_words[max(0, ca - CONTEXT_WORDS):ca])
            after = _join(caption_words[cb:cb + CONTEXT_WORDS])
        else:
            before = _join(whisper_words[max(0, wa - CONTEXT_WORDS):wa])
            after = _join(whisper_words[wb:wb + CONTEXT_WORDS])
        spots.append(Spot(
            start_sec=round(min(x["start"] for x in involved), 2),
            end_sec=round(max(x["end"] for x in involved), 2),
            kind=_kind(caption, whisper, wws),
            caption=caption,
            whisper=whisper,
            before=before,
            after=after,
        ))
    return spots, mismatch


def compare(caption_rows: list[dict], whisper_rows: list[dict], spans: list[tuple[float, float]]) -> dict:
    """들은 범위마다 맞대어 다른 곳 목록과 요약을 돌려준다.

    범위마다 따로 맞대는 이유: 범위는 청크(≤30분)라 글자열 비교 비용이 묶이고, 청크 경계는 무음 지점이라
    경계에서 말이 잘리지 않는다.
    """
    caption_words, whisper_words = _words(caption_rows), _words(whisper_rows)
    spots: list[Spot] = []
    mismatch = caption_chars = whisper_chars = 0
    for a, b in sorted(spans):
        cw = [x for x in caption_words if a <= x["start"] < b]
        ww = [x for x in whisper_words if a <= x["start"] < b]
        found, missed = compare_window(cw, ww)
        spots += found
        mismatch += missed
        caption_chars += len(_stream(cw)[0])
        whisper_chars += len(_stream(ww)[0])
    by_kind = {kind: sum(1 for s in spots if s.kind == kind) for kind in KINDS}
    return {
        "comparedSec": round(sum(b - a for a, b in spans), 1),
        "captionChars": caption_chars,
        "whisperChars": whisper_chars,
        "mismatchChars": mismatch,
        # 긴 쪽 글자 수 대비. 2026-11-01 실측 기준선: 서강대 5분 9.1% · 삼성 31분 9.2%.
        "mismatchRatio": round(mismatch / max(1, caption_chars, whisper_chars), 4),
        "byKind": by_kind,
        "spots": [asdict(s) for s in sorted(spots, key=lambda s: s.start_sec)],
    }


def for_source(conn: psycopg.Connection, source_id: int) -> dict:
    """원본 하나의 비교. 두 대사가 다 있어야 한다 — 없으면 `available: False` 와 이유."""
    spans = [(r["start_sec"], r["end_sec"]) for r in conn.execute(
        "select start_sec, end_sec from stt_spans where source_id = %s order by start_sec", (source_id,)
    )]
    if not spans:
        return {"available": False, "reason": "음성 인식을 아직 하지 않았습니다"}
    caption_rows = transcripts.rows_of(conn, source_id, "youtube")
    if not caption_rows:
        return {"available": False, "reason": "유튜브 자막을 아직 가져오지 않았습니다"}
    return {"available": True, **compare(caption_rows, transcripts.rows_of(conn, source_id, "whisper"), spans)}
