"""숏폼 카피 후보 — AI 카피(tease §5-10 "AI 카피 후보", P4 첫 단계, 2026-11-08).

편집 화면의 [AI 카피 후보] 가 부른다. 클립 대사 · 제목 · 영상 개요(3층) · 관리자 기준(2층) · 그 클립을 고른 run 의 컨셉
(4층, tease §5-11 — 바이럴은 세게, 정보성은 무엇을 알려 주는지)으로 카피 후보 다섯을 받는다.
Gemini 1회. 저장하지 않는다 — 사람이 하나를 고르면 카피 칸에 들어가고, 거기서 고친다.

🔴 코드가 검증한다(LLM 출력은 항상 검증한다):
    줄 수가 템플릿과 다르다       → 버린다
    빈 줄이 있다                  → 버린다
    대사 · 제목에 없는 숫자가 있다 → 버린다. 카피의 숫자는 시청자가 그대로 믿는다 — "합격률 90%" 를 지어내면 그 숏폼은
                                    거짓말이 된다. 프롬프트로 부탁만 해서는 보장되지 않는다(standards.py "약한 신호다")
    근거 구절이 대사에 없다        → 근거만 비운다(후보는 남긴다)
    한 줄이 글자 수를 넘는다       → 표시만 한다(over). 사람이 줄이는 것이 버리는 것보다 낫다
버린 후보와 이유는 stage_calls.params 에 남는다 — 무엇을 왜 버렸는지 되짚을 수 있어야 프롬프트를 고친다.

예시 카피는 넣지 않는다. 예시는 실제 숏폼 카피만 쓴다는 원칙인데(tease §5-10 순서 P4) 아직 모은 것이 없다.
TODO: P3(기존 숏폼 가져오기)에서 모은 카피로 시리즈마다 프롬프트 버전을 쌓는다.
"""

import json
import re
import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config, standards
from ..adapters import gemini
from . import concepts, templates

# 후보 수. 다섯이면 각도(결론 먼저 · 질문 · 숫자 · 말하는 사람 · 반전)가 하나씩 돌아간다 — 설계안 v3 의 P4 기준.
COUNT = 5
# 대사 길이 상한(글자). 3분(쇼츠 상한) 대사가 1,500자 안팎이다 — 그보다 훨씬 길면 화면의 버그다.
TRANSCRIPT_MAX = 6000
# 근거 구절 상한(글자). 길면 근거가 아니라 대사를 통째로 옮긴 것이다.
QUOTE_MAX = 60

PROMPT = """너는 유튜브 쇼츠 카드의 카피를 쓴다. 카드는 위에서 아래로 시리즈 배지 · 카피 {lines}줄 · 영상 · 출처를 쌓는다.
카피는 영상보다 먼저 읽힌다. 스크롤을 멈추게 하되, 영상에서 실제로 하는 말과 어긋나면 안 된다.

규칙:
- 후보를 {count}개 낸다. 서로 다른 각도로 쓴다(예: 결론 먼저 · 질문 · 숫자 · 말하는 사람 · 반전).
- 후보마다 lines 는 정확히 {lines}줄이다.
{roles}
- 한 줄은 공백을 빼고 {max_chars}자 이하다. 넘칠 것 같으면 더 짧은 말을 고른다.
- 대사에 나온 사실만 쓴다. 숫자는 대사나 클립 제목에 나온 숫자만 쓴다.
- quote 에는 그 카피의 근거가 된 대사 한 구절을 대사에서 글자 그대로 옮긴다({quote_max}자 이내).
- angle 에는 그 후보의 각도를 짧게 적는다(예: "결론 먼저").
{standard_block}{concept_block}{context_block}
클립 제목: {title}

대사:
{transcript}
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lines": {"type": "array", "items": {"type": "string"}},
                    "angle": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["lines", "angle", "quote"],
            },
        }
    },
    "required": ["candidates"],
}

# 숫자 한 덩어리. "1,000" · "3.5" 도 한 덩어리로 읽는다.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


class HeadlineError(RuntimeError):
    pass


class NothingToWrite(HeadlineError):
    """부르기 전에 막힌 것(카피 자리 · 대사가 없다) — 모델 탓이 아니라 입력 탓이다(422)."""


def _roles(block: dict) -> str:
    """줄마다 하는 일. 띠가 있는 줄(잡소리 · 왓츠인 마이 AI 의 둘째 줄)이 결론을 받는다."""
    out = []
    for index, line in enumerate(block["lines"]):
        role = (
            "검은 띠 위의 흰 글자 — 결론이나 가장 강한 말" if line.get("band") else "글자만 — 상황 · 대상 · 말하는 사람"
        )
        out.append(f"- {index + 1}줄: {role}")
    return "\n".join(out)


def build_prompt(
    block: dict, *, title: str | None, context: str | None, standard: str | None, transcript: str,
    concept: str | None = None,
) -> str:
    return PROMPT.format(
        lines=len(block["lines"]), count=COUNT, roles=_roles(block), max_chars=block["max_chars"], quote_max=QUOTE_MAX,
        standard_block=standards.block(standard), concept_block=concepts.block(concept, "copy"),
        context_block=f"\n영상 개요: {context.strip()}\n" if context and context.strip() else "",
        title=(title or "").strip() or "(없음)", transcript=transcript,
    )


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _has_number(number: str, source: str) -> bool:
    plain = number.replace(",", "")
    # 앞뒤가 숫자가 아닌 자리에서만 맞춘다 — "3" 이 "30" 안에서, "5" 가 "0.5" 안에서 맞으면 지어낸 숫자가 통과한다.
    # 앞의 점 하나는 숫자의 일부일 때만 막는다("ep.48" 의 48 은 통과).
    return re.search(rf"(?<!\d)(?<!\d\.){re.escape(plain)}(?![\d.]*\d)", source) is not None


def check(raw: str, *, block: dict, transcript: str, title: str | None) -> tuple[list[dict], list[dict]]:
    """응답을 읽고 검증한다. (남긴 후보, 버린 후보와 이유). 응답 전체가 깨졌으면 HeadlineError — 호출부가 한 번 더 부른다."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HeadlineError(f"AI 응답이 JSON 이 아니다: {exc}") from exc
    items = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise HeadlineError("AI 응답에 candidates 가 없다")

    count = len(block["lines"])
    max_chars = int(block["max_chars"])
    numbers_from = (transcript + " " + (title or "")).replace(",", "")
    spoken = _squash(transcript)
    kept: list[dict] = []
    dropped: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("lines"), list):
            dropped.append({"lines": None, "why": "모양이 틀렸다"})
            continue
        lines = [" ".join(str(line).split()) for line in item["lines"]]
        if len(lines) != count:
            dropped.append({"lines": lines, "why": f"줄 수가 {len(lines)}이다({count}줄이어야 한다)"})
            continue
        if any(not line for line in lines):
            dropped.append({"lines": lines, "why": "빈 줄이 있다"})
            continue
        missing = [n for n in _NUMBER.findall(" ".join(lines)) if not _has_number(n, numbers_from)]
        if missing:
            dropped.append({"lines": lines, "why": f"대사에 없는 숫자 {', '.join(missing)}"})
            continue
        if tuple(lines) in seen:
            continue
        seen.add(tuple(lines))
        quote = " ".join(str(item.get("quote") or "").split())
        kept.append({
            "lines": lines,
            "angle": " ".join(str(item.get("angle") or "").split())[:20],
            # 대사에 없는 근거는 근거가 아니다 — 띄어쓰기만 다른 것은 같은 구절로 본다.
            "quote": quote if quote and len(quote) <= QUOTE_MAX and _squash(quote) in spoken else None,
            "over": [index for index, line in enumerate(lines) if len(_squash(line)) > max_chars],
        })
    return kept[:COUNT], dropped


def run(
    conn: psycopg.Connection, cfg: config.Config, *, source_id: int, clip_id: int, spec: dict,
    title: str | None, context: str | None, transcript: str, concept: str | None = None,
) -> dict:
    """카피 후보를 받는다. 돌려주는 것: candidates(남긴 후보) · dropped(버린 수) · latencyMs. 저장하지 않는다.

    실패하면 HeadlineError(기록하고 올린다) — 카피 칸은 그대로이고 화면이 이유를 보인다.
    """
    block = templates.block_of(spec, "headline")
    if block is None or not block["lines"]:
        raise NothingToWrite("이 템플릿에는 카피 자리가 없다")
    transcript = " ".join(transcript.split())
    if not transcript:
        raise NothingToWrite("이 구간에는 대사가 없어 카피를 쓸 수 없다")
    if len(transcript) > TRANSCRIPT_MAX:
        raise NothingToWrite(f"대사가 {TRANSCRIPT_MAX}자를 넘는다 — 구간을 줄인 뒤 다시 누른다")
    prompt = build_prompt(
        block, title=title, context=context, standard=standards.load(conn), transcript=transcript, concept=concept
    )
    # 🔴 모델 호출이 몇 초다 — 그동안 트랜잭션을 붙잡지 않는다.
    conn.commit()

    params = {"clip_id": clip_id, "lines": len(block["lines"]), "max_chars": block["max_chars"],
              "transcript_chars": len(transcript), "concept": concept}
    started = time.monotonic()
    result: tuple[list[dict], list[dict]] | None = None
    call_id = None
    # 🔴 응답 전체가 깨졌을 때만 한 번 더 부른다(LLM 출력 규약). 후보 몇이 검증에서 빠진 것은 다시 부르지 않는다.
    for attempt in (1, 2):
        try:
            raw, usage, latency_ms = gemini.generate_json(cfg, prompt, RESPONSE_SCHEMA)
        except Exception as exc:
            _record(conn, source_id, None, None, params | {"attempt": attempt}, f"{type(exc).__name__}: {exc}"[-500:],
                    cfg, prompt)
            conn.commit()
            raise HeadlineError(f"AI 가 답하지 못했다: {exc}") from exc
        call_id = _record(conn, source_id, usage, latency_ms, params | {"attempt": attempt}, None, cfg, prompt, raw)
        conn.commit()
        try:
            result = check(raw, block=block, transcript=transcript, title=title)
            break
        except HeadlineError as exc:
            conn.execute("update stage_calls set error = %s where id = %s", (str(exc), call_id))
            conn.commit()
            if attempt == 2:
                raise

    if result is None:  # 위 반복이 break 없이 끝나는 길은 없다 — 타입 검사기를 위한 줄
        raise HeadlineError("AI 응답을 읽지 못했다")
    kept, dropped = result
    conn.execute(
        "update stage_calls set params = params || %s where id = %s",
        (Jsonb({"kept": len(kept), "dropped": dropped}), call_id),
    )
    conn.commit()
    return {"candidates": kept, "dropped": len(dropped), "latencyMs": int((time.monotonic() - started) * 1000)}


def _record(
    conn: psycopg.Connection, source_id: int, usage: dict | None, latency_ms: int | None, params: dict,
    error: str | None, cfg: config.Config, prompt: str, raw: str | None = None,
) -> int:
    usage = usage or {}
    return conn.execute(
        """insert into stage_calls
           (source_id, stage, model, input_tokens, output_tokens, thinking_tokens, total_tokens, cached_tokens,
            latency_ms, prompt, response, params, error)
           values (%s, 'copy', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (
            source_id, cfg.gemini_model, usage.get("input_tokens"), usage.get("output_tokens"),
            usage.get("thinking_tokens"), usage.get("total_tokens"), usage.get("cached_tokens"), latency_ms,
            prompt if cfg.store_prompts else None, raw if cfg.store_prompts else None, Jsonb(params), error,
        ),
    ).fetchone()["id"]
