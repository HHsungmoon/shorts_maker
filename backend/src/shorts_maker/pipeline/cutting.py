"""A6 — [6] 구간 잘라내기 (문서 §4-[6]).

구간이 통째로 쓰기엔 길어서 안에서 30~60초를 고른다.

🔴 모델은 **발화 인덱스만** 고른다. 초는 utterances 에서 되찾는다(§12). 그래서 말 중간에서
잘리는 일이 구조적으로 없다 — 발화 경계가 곧 컷 지점이다.
"""

import dataclasses
import json
from dataclasses import dataclass

import psycopg

from .. import config, standards
from ..adapters import gemini
from . import concepts

TARGET_MIN_SEC = 30.0
TARGET_MAX_SEC = 60.0


class CuttingError(RuntimeError):
    pass


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "start_idx": {"type": "INTEGER"},
        "end_idx": {"type": "INTEGER"},
        "reason": {"type": "STRING"},
    },
    "required": ["start_idx", "end_idx", "reason"],
}

PROMPT_TEMPLATE = """다음은 한 강연 구간의 발화 목록이다. 각 줄은 `[번호] (길이초) 발화` 형식이다.

이 안에서 **{min_sec:.0f}~{max_sec:.0f}초 분량의 연속 구간**을 하나 골라라.
숏폼 클립으로 그대로 쓸 부분이다.

규칙:
- start_idx 와 end_idx 는 아래 목록에 있는 번호여야 하고, start_idx <= end_idx 다.
- 고른 구간의 길이 합이 {min_sec:.0f}~{max_sec:.0f}초에 들어가게 맞춰라.
- 말이 도중에 시작하거나 끝나지 않게, 문장이 완결되는 지점을 고른다.
- 앞을 듣지 않아도 이해되는 지점에서 시작한다.
- reason 은 왜 이 구간인지 한두 문장.

{standard_block}{concept_block}{context_block}구간 요약: {description}

발화 목록:
{utterance_block}"""


@dataclass
class Cut:
    start_idx: int
    end_idx: int
    reason: str


def build_prompt(
    utterances: list[dict], description: str, context: str | None, standard: str | None = None,
    concept: str | None = None,
) -> str:
    """`concept` 는 그 run 의 컨셉(concepts.py, tease §5-11). 바이럴은 센 한마디가 앞에, 정보성은 정보가 끝까지 —
    컨셉마다 자르는 자리가 다르다. 없으면 그 줄이 빠진다."""
    if not utterances:
        raise CuttingError("발화가 없다")
    lines = "\n".join(
        f"[{u['idx']}] ({u['end_sec'] - u['start_sec']:.0f}초) {u['text']}" for u in utterances
    )
    context_block = f"강연 개요: {context.strip()}\n\n" if context and context.strip() else ""
    return PROMPT_TEMPLATE.format(
        min_sec=TARGET_MIN_SEC,
        max_sec=TARGET_MAX_SEC,
        context_block=context_block,
        standard_block=standards.block(standard),
        concept_block=concepts.block(concept, "cut"),
        description=description,
        utterance_block=lines,
    )


def parse_response(raw: str, valid_idx: set[int]) -> Cut:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CuttingError(f"JSON 파싱 실패: {exc}") from exc
    try:
        cut = Cut(int(payload["start_idx"]), int(payload["end_idx"]), str(payload["reason"]).strip())
    except (KeyError, TypeError, ValueError) as exc:
        raise CuttingError(f"응답 형식이 잘못됐다: {payload!r}") from exc
    if cut.start_idx not in valid_idx or cut.end_idx not in valid_idx:
        raise CuttingError(
            f"구간 밖의 발화를 지목했다: {cut.start_idx}~{cut.end_idx} "
            f"(허용 {min(valid_idx)}~{max(valid_idx)})"
        )
    if cut.start_idx > cut.end_idx:
        raise CuttingError(f"start_idx 가 end_idx 보다 크다: {cut.start_idx} > {cut.end_idx}")
    return cut


def load_segment_utterances(conn: psycopg.Connection, segment: dict) -> list[dict]:
    rows = conn.execute(
        """select idx, start_sec, end_sec, text from utterances
           where chunk_id = %s and idx between %s and %s order by idx""",
        (segment["chunk_id"], segment["start_utterance_idx"], segment["end_utterance_idx"]),
    ).fetchall()
    return [dict(r) for r in rows]


def existing_clip(conn: psycopg.Connection, run_id: int, segment_id: int) -> dict | None:
    return conn.execute(
        "select * from clips where run_id = %s and segment_id = %s", (run_id, segment_id)
    ).fetchone()


def create_clip(
    conn: psycopg.Connection, run_id: int, segment: dict, utterances: list[dict], cut: Cut,
    score: float | None, replace: bool = False,
) -> int:
    by_idx = {u["idx"]: u for u in utterances}
    start_sec = by_idx[cut.start_idx]["start_sec"]
    end_sec = by_idx[cut.end_idx]["end_sec"]

    previous = existing_clip(conn, run_id, segment["id"])
    if previous is not None:
        if not replace:
            # 🔴 조용히 하나 더 만들면 어느 게 최신인지 알 수 없게 된다. DB 유니크 인덱스가
            # 막고 있지만, 여기서 먼저 사람이 읽을 수 있는 이유로 돌려준다.
            raise CuttingError(
                f"이 구간으로 만든 클립 {previous['id']} 이 이미 있다 — 다시 만들려면 replace 를 준다"
            )
        # 파일은 남겨둔다. 렌더 결과를 지우는 건 렌더 단계의 일이고, 여기서 지우면
        # 실패 시 아무것도 남지 않는다.
        conn.execute("delete from clips where id = %s", (previous["id"],))

    cursor = conn.execute(
        # 🔴 total_sec 은 여기서도 채운다. 조각 하나짜리라 봉투와 같은 값이지만, "실제 길이는
        # total_sec 이다" 라는 규칙에 예외를 두면 읽는 쪽이 매번 null 을 처리해야 한다.
        """insert into clips (run_id, segment_id, start_sec, end_sec, score, reason, total_sec, title)
           values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        # 제목의 기본값은 구간 설명이다. "…를 설명한다" 형태라 제목으로 완벽하지 않지만 빈 칸보다
        # 낫고, 크리에이터가 고칠 수 있다(PATCH /api/clips/{id}).
        (run_id, segment["id"], start_sec, end_sec, score, cut.reason, end_sec - start_sec,
         (segment.get("description") or "").strip()[:200] or None),
    )
    clip_id = cursor.fetchone()["id"]
    conn.commit()
    return clip_id


def run_for_segment(
    conn: psycopg.Connection, cfg: config.Config, run_id: int, segment_id: int,
    score: float | None, manual: tuple[int, int] | None = None, replace: bool = False,
) -> int:
    segment = conn.execute("select * from segments where id = %s", (segment_id,)).fetchone()
    if segment is None:
        raise CuttingError(f"segment {segment_id} 없음")
    utterances = load_segment_utterances(conn, segment)
    valid = {u["idx"] for u in utterances}

    if manual is not None:
        # LLM 없이 범위를 직접 지정하는 경로. A7 렌더를 키 없이 검증할 때 쓴다.
        # 🔴 편의 경로지 rank 결과가 아니다 — reason 에 그렇게 남긴다.
        start, end = manual
        if start not in valid or end not in valid or start > end:
            raise CuttingError(f"수동 범위가 구간 밖이다: {start}~{end} (허용 {min(valid)}~{max(valid)})")
        cut = Cut(start, end, f"수동 지정 ({start}~{end})")
        return create_clip(conn, run_id, segment, utterances, cut, score, replace)

    if not replace and existing_clip(conn, run_id, segment_id) is not None:
        # 🔴 LLM 을 부르기 **전에** 막는다. 부르고 나서 거절하면 그 호출은 그냥 낭비다.
        raise CuttingError(f"이 구간으로 만든 클립이 이미 있다 — 다시 만들려면 replace 를 준다")

    source_id = conn.execute(
        "select source_id from chunks where id = %s", (segment["chunk_id"],)
    ).fetchone()["source_id"]
    context = conn.execute("select context from sources where id = %s", (source_id,)).fetchone()["context"]
    # 컨셉은 run 이 들고 있다 — 고를 때 쓴 컨셉으로 자른다(tease §5-11). 질문 경로 · 컨셉 이전의 run 은 null.
    run = conn.execute("select concept from runs where id = %s", (run_id,)).fetchone()
    concept = run["concept"] if run else None

    # 관리자 기준(2층). 구간 안에서 어디를 자를지는 선호의 문제라 여기에 넣는다.
    prompt = build_prompt(utterances, segment["description"] or "", context, standards.load(conn), concept)
    try:
        raw, usage, latency_ms = gemini.generate_json(cfg, prompt, RESPONSE_SCHEMA)
    except Exception as exc:
        conn.execute(
            """insert into stage_calls (source_id, run_id, segment_id, stage, model, error)
               values (%s, %s, %s, 'cut', %s, %s)""",
            (source_id, run_id, segment_id, cfg.gemini_model, f"{type(exc).__name__}: {exc}"),
        )
        conn.commit()
        raise
    call = conn.execute(
        """insert into stage_calls
           (source_id, run_id, segment_id, stage, model, input_tokens, output_tokens,
            thinking_tokens, total_tokens, cached_tokens, latency_ms, prompt, response)
           values (%s, %s, %s, 'cut', %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (
            source_id, run_id, segment_id, cfg.gemini_model,
            usage["input_tokens"], usage["output_tokens"], usage["thinking_tokens"],
            usage["total_tokens"], usage["cached_tokens"], latency_ms,
            prompt if cfg.store_prompts else None,
            raw if cfg.store_prompts else None,
        ),
    )
    call_id = call.fetchone()["id"]
    conn.commit()

    try:
        cut = parse_response(raw, valid)
    except CuttingError as exc:
        conn.execute("update stage_calls set error = %s where id = %s", (str(exc), call_id))
        conn.commit()
        raise
    return create_clip(conn, run_id, segment, utterances, cut, score, replace)


# ==================================================================== 답하기 경로
#
# 위의 run_for_segment 는 "구간 하나 안에서 30~60초를 골라줘" 를 LLM 에게 묻는다. 답하기 경로에서는
# 그 결정을 rank 가 이미 했다(ranking.plan_answer 가 조각의 발화 범위를 준다) — 그래서 여기서는
# **LLM 을 부르지 않는다.** 초를 되찾고, 예산을 강제하고, 행을 만든다.

@dataclass
class PartSpec:
    """클립 조각 하나. 초는 발화에서 되찾은 값이지 LLM 이 말한 값이 아니다(§12)."""

    segment_id: int
    chunk_id: int
    start_utterance_idx: int
    end_utterance_idx: int
    start_sec: float
    end_sec: float
    text: str

    @property
    def length(self) -> float:
        return self.end_sec - self.start_sec


# 🔴 **앞을 가리키는 표현.** 클립이 여기서 시작하면 앞을 못 본 시청자에게는 뜻이 없다.
#
# 실측 근거(2026-09-09, 클러스터 118 "개발 직군의 주요 기술 스택"): 후보 **셋이 전부** 자립성
# 판정에서 떨어졌고 이유가 같았다 — "저희는 **이런 서비스들**을 …", "**이렇게** 수많은 AI
# 프로젝트들이 …". 검색도 rank 도 맞는 대사를 골랐는데 **시작점이 한 발화 늦었다.**
#
# 프롬프트에는 이미 "지시대명사로 시작하지 않는다" 가 있었고 지켜지지 않았다. 그래서 규약대로
# **모델에게 다시 부탁하는 대신 코드가 고친다**(make_shorts §12).
LEAD_IN_POINTERS = (
    # 앞의 것을 가리키는 말 — 선행사가 클립 밖에 있으면 뜻이 통하지 않는다
    "이런", "그런", "저런", "이러한", "그러한",
    "이렇게", "그렇게", "저렇게",
    "이것", "그것", "저것", "이거", "그거", "저거", "이게", "그게", "저게",
    "여기", "거기",
    # 앞에서 이미 말했다고 명시하는 말
    "아까", "앞서", "방금", "말씀드린", "말씀드렸", "설명드린",
)
LEAD_IN_CONNECTIVES = (
    # 이어 말하는 접속 — 문장 자체는 서지만 이야기 중간에서 시작한 티가 난다
    "그래서", "그러니까", "그러면", "그럼", "그리고", "그런데", "근데",
    "또한", "이어서", "다음으로", "마지막으로", "결국",
)
LEAD_IN_MARKERS = LEAD_IN_POINTERS + LEAD_IN_CONNECTIVES

# 🔴 **첫머리만 본다.** 클립 한복판에 지시어가 나오는 건 정상이다 — 그때는 선행사가 클립 안에 있다.
# 앞에서 이만큼만 훑는 이유가 그것이고, 실측 사례의 "저희는 이런 서비스들을" 은 4자째에서 걸린다.
LEAD_IN_SCAN_CHARS = 25

# 🔴 몇 발화까지 거슬러 올라가며 시작점을 찾을까. 그 위로는 30초 예산 안에서 답이 들어갈 자리가 없다.
MAX_LEAD_IN = 3

# 한국어 문장이 끝나는 어미. **착지 지점을 고르는 데만** 쓴다.
#
# 🔴 이게 없으면 지시어만 피하다가 문장 한복판에 떨어진다. 실측에서 실제로 그랬다 —
# "이렇게 수많은 AI 프로젝트들이" 를 피해 당겼더니 "해나가고 있고" 에서 시작했다.
# 지시어는 없지만 그게 더 나쁘다.
SENTENCE_ENDINGS = (
    "다", "요", "죠", "까", "네", "군", "니", "라", "자", "죠",
)


def needs_lead_in(text: str) -> bool:
    """이 발화로 클립을 시작하면 앞 맥락이 필요한가."""
    head = (text or "").strip()[:LEAD_IN_SCAN_CHARS]
    return any(marker in head for marker in LEAD_IN_MARKERS)


def points_back(text: str) -> bool:
    """첫머리가 **앞의 것을 가리키는가** — 접속어("그래서·근데")는 세지 않는다.

    🔴 needs_lead_in 보다 무른 기준이다. 접속어로 시작하는 발화는 이야기 중간에서 시작한 티는 나도
    뜻은 선다 — 무엇을 가리키는지 모르는 "이런·아까" 와는 다르다. 후보를 **여럿 만들어 판정에 맡기는**
    쪽(answers/proposals)이 이 기준을 쓴다. 하나만 고치는 lead_in 은 그대로 needs_lead_in 을 쓴다.
    """
    head = (text or "").strip()[:LEAD_IN_SCAN_CHARS]
    return any(marker in head for marker in LEAD_IN_POINTERS)


def ends_sentence(text: str) -> bool:
    """이 발화가 문장을 끝내는가. 다음 발화가 새 문장으로 시작한다는 신호다."""
    tail = (text or "").strip().rstrip(".?!… \"'")
    return bool(tail) and tail.endswith(SENTENCE_ENDINGS)


def lead_in(part_ranges: list, lines: list[dict], max_sec: float) -> list:
    """앞을 가리키며 시작하는 조각의 **시작점을 앞으로 당긴다.**

    조각마다 따로 본다 — 조합 클립은 조각 사이에 점프가 있어서 두 번째 조각도 그 자리에서
    새로 시작하는 셈이다(실측 사례의 combo 가 정확히 그랬다).

    **좋은 시작점**의 뜻이 둘이다: ① 앞을 가리키지 않고 ② 앞 발화가 문장을 끝냈다.
    ②가 없으면 지시어만 피하다 문장 한복판에 떨어진다(`SENTENCE_ENDINGS` 주석의 실측).

    🔴 **좋은 자리를 못 찾으면 그냥 둔다.** 어중간하게 당겨 놓는 것보다 원래대로 두는 편이 낫다 —
    남은 어색함은 판정이 잡아내고 크리에이터가 화면에서 대사를 읽고 판단한다.

    🔴 **맥락 때문에 답을 잘라내지 않는다.** 예산을 넘기면 당기지 않는다. 앞머리만 붙고 답이
    반이 된 클립보다, 시작이 조금 어색해도 답이 온전한 편이 낫다.

    🔴 구간을 넘지 않고 앞 조각과 겹치지도 않는다. 둘 다 `parse_answer` 가 세운 불변식이다.
    """
    if not part_ranges:
        return part_ranges
    by_line = {line["line"]: line for line in lines}
    length_of = {n: line["end_sec"] - line["start_sec"] for n, line in by_line.items()}
    total = sum(
        length_of.get(n, 0.0)
        for part in part_ranges
        for n in range(part.start_line, part.end_line + 1)
    )

    def good_start(n: int, floor: int) -> bool:
        line = by_line.get(n)
        if line is None or needs_lead_in(line["text"]):
            return False
        before = by_line.get(n - 1)
        # 앞이 없거나(목록의 처음), 앞 조각에 막혔거나, 앞이 문장을 끝냈으면 새 문장의 시작이다.
        if before is None or n - 1 <= floor:
            return True
        if before["segment_id"] != line["segment_id"]:
            return True
        return ends_sentence(before["text"])

    out: list = []
    floor = -1  # 이 번호 아래로는 못 내려간다 — 앞 조각의 끝
    for part in part_ranges:
        start = part.start_line
        head = by_line.get(start)
        if head is not None and needs_lead_in(head["text"]):
            grew = 0.0
            for step in range(1, MAX_LEAD_IN + 1):
                candidate = start - step
                previous = by_line.get(candidate)
                if previous is None or candidate <= floor:
                    break
                if previous["segment_id"] != head["segment_id"]:
                    break
                grew += length_of.get(candidate, 0.0)
                if total + grew > max_sec:
                    break
                if good_start(candidate, floor):
                    start = candidate
                    total += grew
                    break
        out.append(dataclasses.replace(part, start_line=start))
        floor = part.end_line
    return out


def resolve_parts(part_ranges: list, lines: list[dict]) -> list[PartSpec]:
    """rank 가 고른 번호 범위를 실제 초와 대사로 바꾼다."""
    by_line = {line["line"]: line for line in lines}
    specs: list[PartSpec] = []
    for part in part_ranges:
        span = [by_line[n] for n in range(part.start_line, part.end_line + 1) if n in by_line]
        if not span:
            raise CuttingError(f"조각이 비었다: {part.start_line}~{part.end_line}")
        specs.append(
            PartSpec(
                segment_id=span[0]["segment_id"],
                chunk_id=span[0]["chunk_id"],
                start_utterance_idx=span[0]["utterance_idx"],
                end_utterance_idx=span[-1]["utterance_idx"],
                start_sec=span[0]["start_sec"],
                end_sec=span[-1]["end_sec"],
                text=" ".join(line["text"].strip() for line in span),
            )
        )
    return specs


def enforce_budget(parts: list[PartSpec], max_sec: float, lines: list[dict]) -> list[PartSpec]:
    """길이 합을 예산 안으로 **코드가** 맞춘다. 🔴 LLM 이 말한 길이는 믿지 않는다(tease §5-6).

    순서가 있다: ① 뒤 조각부터 떨어뜨린다(앞이 대개 답의 핵심이다) ② 하나만 남았는데도 넘으면
    **발화 단위로** 뒤를 잘라낸다. 초 단위로 자르지 않는 이유는 말 중간에서 끊기기 때문이다 —
    발화 경계가 곧 컷 지점이라는 원칙이 여기서도 유지된다.
    """
    if not parts:
        raise CuttingError("조각이 없다")
    kept = list(parts)
    while len(kept) > 1 and sum(p.length for p in kept) > max_sec:
        kept.pop()
    if sum(p.length for p in kept) <= max_sec:
        return kept

    # 하나 남았는데 여전히 길다 — 뒤에서부터 발화를 덜어낸다.
    only = kept[0]
    span = [
        line for line in lines
        if line["chunk_id"] == only.chunk_id
        and only.start_utterance_idx <= line["utterance_idx"] <= only.end_utterance_idx
    ]
    while len(span) > 1 and span[-1]["end_sec"] - span[0]["start_sec"] > max_sec:
        span.pop()
    return [
        PartSpec(
            segment_id=only.segment_id,
            chunk_id=only.chunk_id,
            start_utterance_idx=span[0]["utterance_idx"],
            end_utterance_idx=span[-1]["utterance_idx"],
            start_sec=span[0]["start_sec"],
            end_sec=span[-1]["end_sec"],
            text=" ".join(line["text"].strip() for line in span),
        )
    ]


def create_answer_clip(
    conn: psycopg.Connection, run_id: int, parts: list[PartSpec], score: float | None, reason: str,
    title: str | None = None,
) -> int:
    """조각들로 클립 하나를 만든다. 단일 컷도 조각 1개 — 코드 경로가 하나다(tease §5-6).

    `clips.start_sec/end_sec` 은 조합 클립에서 **봉투**다(첫 조각 시작 ~ 마지막 조각 끝).
    실제 길이는 `total_sec` 이고, 재생되는 건 조각들의 합이다.
    """
    if not parts:
        raise CuttingError("조각이 없다")
    total = sum(p.length for p in parts)
    clip_id = conn.execute(
        """insert into clips (run_id, segment_id, start_sec, end_sec, score, reason, total_sec, title)
           values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (run_id, parts[0].segment_id, parts[0].start_sec, parts[-1].end_sec, score, reason, total,
         (title or "").strip() or None),
    ).fetchone()["id"]
    for ordinal, part in enumerate(parts):
        conn.execute(
            """insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec,
                                       start_utterance_idx, end_utterance_idx)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            (clip_id, ordinal, part.segment_id, part.start_sec, part.end_sec,
             part.start_utterance_idx, part.end_utterance_idx),
        )
    return clip_id


# ---------------------------------------------------------------- 앞뒤 다듬기 (편집 화면, tease §5-10 "구간 앞뒤 다듬기")

# 조각 하나의 최소 길이. 1초보다 짧은 조각은 사람이 의도한 것이 아니라 버튼을 너무 많이 누른 것이다.
TRIM_MIN_PART_SEC = 1.0
# 유튜브 쇼츠의 길이 상한(3분). 그보다 길면 쇼츠로 올라가지 않는다.
TRIM_MAX_CLIP_SEC = 180.0


def _trim_rows(conn: psycopg.Connection, clip_id: int) -> tuple[dict, list[dict]]:
    clip = conn.execute(
        """select cl.id, cl.start_sec, cl.end_sec, sg.chunk_id
           from clips cl join segments sg on sg.id = cl.segment_id where cl.id = %s""",
        (clip_id,),
    ).fetchone()
    if clip is None:
        raise CuttingError(f"clip {clip_id} 없음")
    # 조각의 구간이 지워졌으면(segment_id 가 null) 청크는 클립의 것으로 본다.
    parts = conn.execute(
        """select p.id, p.ordinal, p.start_sec, p.end_sec, p.start_utterance_idx, p.end_utterance_idx,
                  coalesce(sg.chunk_id, %s) as chunk_id
           from clip_parts p left join segments sg on sg.id = p.segment_id
           where p.clip_id = %s order by p.ordinal""",
        (clip["chunk_id"], clip_id),
    ).fetchall()
    return dict(clip), [dict(p) for p in parts]


def _bounds(conn: psycopg.Connection, clip: dict, parts: list[dict]) -> tuple[float, float]:
    first_chunk = parts[0]["chunk_id"] if parts else clip["chunk_id"]
    last_chunk = parts[-1]["chunk_id"] if parts else clip["chunk_id"]
    low = conn.execute("select start_sec from chunks where id = %s", (first_chunk,)).fetchone()["start_sec"]
    high = conn.execute("select end_sec from chunks where id = %s", (last_chunk,)).fetchone()["end_sec"]
    return float(low), float(high)


def trim_bounds(conn: psycopg.Connection, clip_id: int) -> tuple[float, float]:
    """처음 · 끝을 옮길 수 있는 범위 — 첫 조각 청크의 시작 ~ 마지막 조각 청크의 끝(원본 절대 초)."""
    clip, parts = _trim_rows(conn, clip_id)
    return _bounds(conn, clip, parts)


def trim_ranges(conn: psycopg.Connection, clip_id: int, start: float, end: float) -> list[tuple[float, float]]:
    """처음을 start, 끝을 end 로 옮긴 구간들(`render.clip_ranges` 와 같은 모양). DB 는 바꾸지 않는다.

    조합 클립은 첫 조각의 처음과 마지막 조각의 끝만 옮긴다 — 가운데 이음매는 그대로다.
    🔴 그 조각의 **청크 안에서만** 옮긴다. 청크 밖에는 대사가 없어 자막과 발화 번호를 만들 수 없다.
    """
    clip, parts = _trim_rows(conn, clip_id)
    ranges = (
        [[float(p["start_sec"]), float(p["end_sec"])] for p in parts]
        if len(parts) > 1
        else [[float(clip["start_sec"]), float(clip["end_sec"])]]
    )
    low, high = _bounds(conn, clip, parts)
    start, end = round(float(start), 3), round(float(end), 3)
    if start < low - 0.001 or end > high + 0.001:
        raise CuttingError(
            f"구간은 대사를 받은 범위({_clock(low)}–{_clock(high)}) 안에서만 옮길 수 있다"
        )
    ranges[0][0] = start
    ranges[-1][1] = end
    for index, (a, b) in enumerate(ranges):
        if b - a < TRIM_MIN_PART_SEC:
            where = "클립" if len(ranges) == 1 else f"조각 {index + 1}"
            raise CuttingError(f"{where}이 {TRIM_MIN_PART_SEC:g}초보다 짧아진다")
    total = sum(b - a for a, b in ranges)
    if total > TRIM_MAX_CLIP_SEC:
        raise CuttingError(f"클립이 {TRIM_MAX_CLIP_SEC:g}초(쇼츠 상한)를 넘는다 ({total:.1f}초)")
    return [(a, b) for a, b in ranges]


def apply_trim(conn: psycopg.Connection, clip_id: int, ranges: list[tuple[float, float]]) -> None:
    """`trim_ranges` 가 낸 구간을 `clips` · `clip_parts` 에 쓴다. 커밋하지 않는다 — 명세 저장과 한 트랜잭션이다.

    🔴 조각의 발화 번호도 새 시각으로 다시 찾는다. 번호로 대사를 읽는 곳(답하기 · 판정)이 잘려 나간 문장을 읽지 않게 —
    대사를 갈아 끼울 때 번호를 시각으로 다시 맞추는 것(`transcripts.use`)과 같은 이유다.
    """
    clip, parts = _trim_rows(conn, clip_id)
    start, end = ranges[0][0], ranges[-1][1]
    conn.execute(
        "update clips set start_sec = %s, end_sec = %s, total_sec = %s where id = %s",
        (start, end, round(sum(b - a for a, b in ranges), 3), clip_id),
    )
    if not parts:
        return
    first, last = parts[0], parts[-1]
    first_idx, _ = _utterance_span(conn, first["chunk_id"], start, first["end_sec"] if len(parts) > 1 else end)
    _, last_idx = _utterance_span(conn, last["chunk_id"], last["start_sec"] if len(parts) > 1 else start, end)
    if len(parts) == 1:
        begin = first_idx if first_idx is not None else first["start_utterance_idx"]
        finish = last_idx if last_idx is not None else first["end_utterance_idx"]
        # 대사 없는 틈만 남기면 둘이 엇갈린다 — 끝 번호를 처음 번호에 맞춘다(CHECK end >= start).
        conn.execute(
            """update clip_parts set start_sec = %s, end_sec = %s, start_utterance_idx = %s, end_utterance_idx = %s
               where id = %s""",
            (start, end, begin, max(begin, finish), first["id"]),
        )
        return
    begin = min(first_idx if first_idx is not None else first["start_utterance_idx"], first["end_utterance_idx"])
    finish = max(last_idx if last_idx is not None else last["end_utterance_idx"], last["start_utterance_idx"])
    conn.execute(
        "update clip_parts set start_sec = %s, start_utterance_idx = %s where id = %s", (start, begin, first["id"])
    )
    conn.execute("update clip_parts set end_sec = %s, end_utterance_idx = %s where id = %s", (end, finish, last["id"]))


def _utterance_span(conn: psycopg.Connection, chunk_id: int, start: float, end: float) -> tuple[int | None, int | None]:
    """start~end 에 걸친 발화의 처음 · 마지막 번호. 걸친 발화가 없으면 (None, None)."""
    row = conn.execute(
        "select min(idx) as first, max(idx) as last from utterances where chunk_id = %s and end_sec > %s and start_sec < %s",
        (chunk_id, start, end),
    ).fetchone()
    return row["first"], row["last"]


def _clock(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"
