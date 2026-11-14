"""관리자 숏폼 AI 추천 — 영상을 읽고 컨셉마다 만들 만한 숏폼 주제를 낸다 (tease §5-11, 2026-11-08).

새 클립 탭 오른쪽의 [추천 받기] 가 부른다(잡). 구간 목록(구간 분할의 중립 자산) · 영상 개요(3층) · 관리자 기준(2층)으로
컨셉 셋(`concepts.py`)마다 ① 이 영상이 그 컨셉에 얼마나 맞는지(적합도 0~100 · 한 문장) ② 숏폼 주제 최대 셋(가제 · 구간
하나 · 고를 기준 한 문장 · 이유)을 받는다. Gemini 1회. 결과는 `source_ideas` 에 덧붙인다 — 다시 누르면 새 행이다.

추천은 바로 클립이 되지 않는다. 사람이 주제를 고르면 왼쪽 기준 칸에 컨셉 · 기준이 들어가고 [구간 고르기](ranking)로 간다 —
고르기는 영상 전체에서 다시 점수를 매기므로, 추천한 구간보다 나은 곳이 있으면 그쪽이 위로 온다.

입력이 구간 **요약**인 이유: 95분 영상의 대사는 10만 자를 넘는다. 구간 요약은 고르기(ranking)가 이미 쓰는 입력이라
추천과 고르기가 같은 것을 보고, 추천에서 짚은 구간을 고르기가 못 찾는 일이 없다.

🔴 코드가 검증한다(LLM 출력은 항상 검증한다):
    모르는 컨셉 · 없는 구간 번호 · 빈 가제  → 그 주제를 버린다
    같은 컨셉에 같은 구간이 두 번          → 뒤의 것을 버린다
    컨셉마다 셋을 넘는다                   → 앞의 셋만
    적합도가 0~100 밖이거나 숫자가 아니다   → 그 컨셉의 적합도만 비운다(주제는 남긴다)
버린 수는 결과(`dropped`)와 stage_calls.params 에 남는다. 응답 전체가 깨졌을 때만 한 번 더 부른다.
"""

import json
import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config, standards
from ..adapters import gemini
from . import concepts, ranking

# 컨셉마다 주제 수 상한. 셋이면 컨셉 셋 × 셋 = 아홉 장이 오른쪽 절반에 한 화면으로 들어간다.
PER_CONCEPT = 3
# 글자 상한. 넘으면 자른다 — 가제는 카드 한 줄, 기준은 왼쪽 칸에 그대로 들어가 사람이 고친다.
TITLE_MAX = 40
TEXT_MAX = 200

PROMPT = """다음은 한 영상을 주제 단위로 나눈 구간 목록이다. 이 영상으로 **관리자가 만들 숏폼**을 기획한다.
숏폼의 컨셉은 셋이다:
{concept_list}

할 일:
1. concepts — 컨셉마다 이 영상이 그 컨셉에 얼마나 맞는지 fit(0~100)과 note(한 문장)를 낸다.
   그 컨셉에 맞는 대목이 영상에 거의 없으면 낮게 준다. 세 컨셉을 모두 낸다.
2. ideas — 컨셉마다 숏폼 주제를 최대 {per_concept}개 낸다. 맞는 대목이 적은 컨셉은 적게 내도 된다.
   - concept: 위 컨셉의 키(brand · viral · info)
   - title: 숏폼의 가제. 20자 안팎
   - segment: 그 주제가 담긴 구간 번호 하나. 아래 목록에 있는 번호만 쓴다
   - criteria: 이 주제로 구간을 고를 때 쓸 기준 한 문장. 무엇이 담긴 대목인지 적는다
   - reason: 왜 이 컨셉에 맞는지 한 문장
   - 앞부분을 듣지 않은 사람도 그 구간만 보고 이해할 수 있는 주제만 낸다
   - 같은 컨셉 안에서는 서로 다른 구간으로 낸다
{standard_block}
{context_block}구간 목록:
{segment_block}"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "concepts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "concept": {"type": "STRING", "enum": list(concepts.KEYS)},
                    "fit": {"type": "INTEGER"},
                    "note": {"type": "STRING"},
                },
                "required": ["concept", "fit", "note"],
            },
        },
        "ideas": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "concept": {"type": "STRING", "enum": list(concepts.KEYS)},
                    "title": {"type": "STRING"},
                    "segment": {"type": "INTEGER"},
                    "criteria": {"type": "STRING"},
                    "reason": {"type": "STRING"},
                },
                "required": ["concept", "title", "segment", "criteria", "reason"],
            },
        },
    },
    "required": ["concepts", "ideas"],
}


class IdeaError(RuntimeError):
    pass


def _concept_list() -> str:
    return "\n".join(f"- {c['key']} ({c['label']}): {c['goal']}" for c in concepts.CONCEPTS)


def build_prompt(segments: list[dict], context: str | None, standard: str | None = None) -> str:
    if not segments:
        raise IdeaError("구간이 없다 — 영상 준비(구간 분할)를 먼저 끝낸다")
    lines = "\n".join(
        f"[{s['idx']}] ({s['end_sec'] - s['start_sec']:.0f}초) {s['description'] or ''}".rstrip() for s in segments
    )
    return PROMPT.format(
        concept_list=_concept_list(), per_concept=PER_CONCEPT,
        standard_block=standards.block(standard),
        context_block=f"영상 개요: {context.strip()}\n\n" if context and context.strip() else "",
        segment_block=lines,
    )


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def check(raw: str, segments: list[dict]) -> tuple[dict, list[dict]]:
    """응답을 읽고 검증한다. (저장할 결과, 버린 것과 이유). 응답 전체가 깨졌으면 IdeaError — 호출부가 한 번 더 부른다."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IdeaError(f"AI 응답이 JSON 이 아니다: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("ideas"), list):
        raise IdeaError("AI 응답에 ideas 가 없다")

    by_idx = {s["idx"]: s for s in segments}
    dropped: list[dict] = []

    # 적합도 — 컨셉 셋을 늘 이 순서로 채운다. 모델이 빠뜨린 컨셉은 비운 채로 둔다(화면이 "판단 없음").
    fits: dict[str, dict] = {}
    for item in data.get("concepts") or []:
        if not isinstance(item, dict) or item.get("concept") not in concepts.BY_KEY:
            continue
        key = item["concept"]
        if key in fits:
            continue
        fit = item.get("fit")
        ok = isinstance(fit, int | float) and not isinstance(fit, bool) and 0 <= fit <= 100
        if not ok:
            dropped.append({"concept": key, "why": f"적합도가 0~100 밖이다: {fit!r}"})
        fits[key] = {"fit": int(fit) if ok else None, "note": _text(item.get("note"), TEXT_MAX) or None}
    summary = [{"concept": key, **fits.get(key, {"fit": None, "note": None})} for key in concepts.KEYS]

    ideas: list[dict] = []
    count: dict[str, int] = {}
    seen: set[tuple[str, int]] = set()
    for item in data["ideas"]:
        if not isinstance(item, dict):
            dropped.append({"why": "모양이 틀렸다"})
            continue
        key = item.get("concept")
        title = _text(item.get("title"), TITLE_MAX)
        if key not in concepts.BY_KEY:
            dropped.append({"title": title, "why": f"모르는 컨셉: {key!r}"})
            continue
        try:
            idx = int(item.get("segment"))
        except (TypeError, ValueError):
            idx = None
        if idx is None or idx not in by_idx:
            dropped.append({"title": title, "why": f"없는 구간 번호: {item.get('segment')!r}"})
            continue
        if not title:
            dropped.append({"concept": key, "why": "가제가 비었다"})
            continue
        if (key, idx) in seen:
            dropped.append({"title": title, "why": f"같은 컨셉에 구간 {idx} 가 두 번"})
            continue
        if count.get(key, 0) >= PER_CONCEPT:
            dropped.append({"title": title, "why": f"{key} 주제가 {PER_CONCEPT}개를 넘었다"})
            continue
        seen.add((key, idx))
        count[key] = count.get(key, 0) + 1
        ideas.append({
            "concept": key, "title": title,
            # 🔴 id 와 번호를 둘 다 남긴다. 번호는 프롬프트가 쓴 이름이고, id 는 구간을 다시 나눴는지 알아보는 열쇠다.
            "segment_id": by_idx[idx]["id"], "segment_idx": idx,
            # 기준이 비면 가제로 고른다 — 왼쪽 칸에 무엇이든 들어가야 [구간 고르기] 가 이 주제를 노린다.
            "criteria": _text(item.get("criteria"), TEXT_MAX) or title,
            "reason": _text(item.get("reason"), TEXT_MAX) or None,
        })
    return {"concepts": summary, "ideas": ideas, "dropped": len(dropped)}, dropped


def run_for_source(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> int:
    """추천을 받아 `source_ideas` 에 덧붙인다. 그 행의 id 를 돌려준다. 실패하면 IdeaError(기록하고 올린다)."""
    source = conn.execute("select context from sources where id = %s", (source_id,)).fetchone()
    if source is None:
        raise IdeaError(f"source {source_id} 없음")
    segments = ranking.load_segments(conn, source_id)
    # 관리자 기준(2층) — 어떤 숏폼을 만들지는 선호가 결과를 바꿔야 하는 자리다(standards.py "넣는다").
    prompt = build_prompt(segments, source["context"], standards.load(conn))
    # 🔴 모델 호출이 수십 초다 — 그동안 트랜잭션을 붙잡지 않는다.
    conn.commit()

    params = {"segments": len(segments), "per_concept": PER_CONCEPT}
    started = time.monotonic()
    result: tuple[dict, list[dict]] | None = None
    call_id = None
    for attempt in (1, 2):
        try:
            raw, usage, latency_ms = gemini.generate_json(cfg, prompt, RESPONSE_SCHEMA)
        except Exception as exc:
            _record(conn, cfg, source_id, None, None, params | {"attempt": attempt}, f"{type(exc).__name__}: {exc}"[-500:],
                    prompt)
            conn.commit()
            raise IdeaError(f"AI 가 답하지 못했다: {exc}") from exc
        call_id = _record(conn, cfg, source_id, usage, latency_ms, params | {"attempt": attempt}, None, prompt, raw)
        conn.commit()
        try:
            result = check(raw, segments)
            break
        except IdeaError as exc:
            conn.execute("update stage_calls set error = %s where id = %s", (str(exc), call_id))
            conn.commit()
            if attempt == 2:
                raise

    if result is None:  # 위 반복이 break 없이 끝나는 길은 없다 — 타입 검사기를 위한 줄
        raise IdeaError("AI 응답을 읽지 못했다")
    saved, dropped = result
    conn.execute(
        "update stage_calls set params = params || %s where id = %s",
        (Jsonb({"kept": len(saved["ideas"]), "dropped": dropped,
                "latency_total_ms": int((time.monotonic() - started) * 1000)}), call_id),
    )
    row = conn.execute(
        "insert into source_ideas (source_id, prompt, result) values (%s, %s, %s) returning id",
        (source_id, prompt, Jsonb(saved)),
    ).fetchone()
    conn.commit()
    return row["id"]


def latest(conn: psycopg.Connection, source_id: int, segment_ids: set[int]) -> dict | None:
    """화면용 — 가장 최근 추천. `stale` 은 주제가 가리키던 구간이 지금 없다는 뜻이다(구간을 다시 나눴다)."""
    row = conn.execute(
        "select id, result, created_at from source_ideas where source_id = %s order by id desc limit 1", (source_id,)
    ).fetchone()
    if row is None:
        return None
    result = row["result"] or {}
    ideas = result.get("ideas") or []
    return {
        "id": row["id"], "createdAt": row["created_at"],
        "concepts": result.get("concepts") or [], "ideas": ideas, "dropped": result.get("dropped", 0),
        "stale": any(idea.get("segment_id") not in segment_ids for idea in ideas),
    }


def _record(
    conn: psycopg.Connection, cfg: config.Config, source_id: int, usage: dict | None, latency_ms: int | None,
    params: dict, error: str | None, prompt: str, raw: str | None = None,
) -> int:
    usage = usage or {}
    return conn.execute(
        """insert into stage_calls
           (source_id, stage, model, input_tokens, output_tokens, thinking_tokens, total_tokens, cached_tokens,
            latency_ms, prompt, response, params, error)
           values (%s, 'idea', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (
            source_id, cfg.gemini_model, usage.get("input_tokens"), usage.get("output_tokens"),
            usage.get("thinking_tokens"), usage.get("total_tokens"), usage.get("cached_tokens"), latency_ms,
            prompt if cfg.store_prompts else None, raw if cfg.store_prompts else None, Jsonb(params), error,
        ),
    ).fetchone()["id"]
