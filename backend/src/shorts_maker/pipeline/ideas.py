"""관리자 숏폼 AI 추천 — 영상을 읽고 컨셉마다 만들 만한 숏폼 주제를 낸다 (tease §5-11, 2026-11-08).

새 클립 탭 "AI 추천 클립" 의 [추천 받기] 가 부른다(잡). 구간 목록(구간 분할의 중립 자산) · 영상 개요(3층) · 관리자 기준(2층)으로
컨셉 셋(`concepts.py`)마다 ① 이 영상이 그 컨셉에 얼마나 맞는지(적합도 0~100 · 한 문장) ② 숏폼 주제 최대 다섯(가제 · 구간
하나 · 점수 · 고를 기준 한 문장 · 이유)을 받는다. Gemini 1회. 결과는 `source_ideas` 에 덧붙인다 — 다시 누르면 새 행이다.

주제마다 점수(0~100)를 받고 **65점 이상만 남긴다**(2026-11-14 사용자 결정 — 처음엔 셋까지 점수 없이 냈는데 둘씩만 왔고,
어느 것이 나은지 알 수 없었다). 🔴 문턱은 모델에게 말하지 않는다 — "65 미만은 내지 마라" 고 하면 점수를 부풀려 맞춘다.
점수는 있는 그대로 받고 코드가 자른다. 65 미만은 버린 것(`dropped`)과 따로 센다(`below`) — 틀린 것이 아니라 약한 것이다.

추천 카드에서 바로 [클립 만들기] 다(`cut`, 2026-11-14). 처음엔 주제를 기준 칸에 넣고 [구간 고르기](ranking)로 다시 점수를
매기게 했는데, 고르기가 23개씩 내놓으니 추천 둘이 그 안에 묻혔다. 클립은 run 에 매달리므로 추천 하나가 run 하나다
(`runs.idea_id` · 컨셉 · 기준 = 추천의 기준 문장). 그 run 의 자르기 · AI 카피가 추천의 컨셉을 읽는다.

🔴 이미 있는 대목은 다시 만들지 않는다 — 판단은 구간 단위다. 어느 길(질문 · 직접 고르기 · 추천)에서 나왔든 그 구간을 쓰는
클립이 있으면 ① 추천을 받을 때 그 구간을 **입력에서 뺀다**(모델에게 "만든 것은 빼라" 고 하지 않는다) ② 만들기를 거절한다.
실측 2026-11-14 잡소리 ep.48: 추천 여섯 중 둘이 이미 만든 클립(유튜브에 올린 것 하나 포함)과 같은 구간이었다.

입력이 구간 **요약**인 이유: 95분 영상의 대사는 10만 자를 넘는다. 구간 요약은 고르기(ranking)가 이미 쓰는 입력이라
추천과 고르기가 같은 것을 보고, 추천에서 짚은 구간을 고르기가 못 찾는 일이 없다.

🔴 코드가 검증한다(LLM 출력은 항상 검증한다):
    모르는 컨셉 · 없는 구간 번호 · 빈 가제  → 그 주제를 버린다
    주제 점수가 0~100 밖이거나 숫자가 아니다 → 그 주제를 버린다(점수 없이는 문턱을 넘었는지 모른다)
    주제 점수가 MIN_SCORE 미만            → 뺀다(below 로 센다)
    같은 컨셉에 같은 구간이 두 번          → 점수 높은 것만
    컨셉마다 다섯을 넘는다                 → 점수 높은 다섯만
    적합도가 0~100 밖이거나 숫자가 아니다   → 그 컨셉의 적합도만 비운다(주제는 남긴다)
버린 수는 결과(`dropped`)와 stage_calls.params 에 남는다. 응답 전체가 깨졌을 때만 한 번 더 부른다.
"""

import json
import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config, standards
from ..adapters import gemini
from . import concepts, cutting, ranking

# 컨셉마다 주제 수 상한. 화면은 컨셉 셋을 나란히 세우고 그 아래 카드를 쌓는다. 셋에서 다섯으로 늘렸다(2026-11-14 사용자 결정).
PER_CONCEPT = 5
# 주제 점수 문턱 — 이 점수 이상만 화면에 남긴다(2026-11-14 사용자 결정). 결과에 그때의 값을 함께 남긴다(min_score).
MIN_SCORE = 65
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
   - score: 그 구간으로 이 컨셉의 숏폼을 만들면 얼마나 좋을지(0~100). 주제끼리 비교할 수 있게 매긴다
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
                    "score": {"type": "INTEGER"},
                    "criteria": {"type": "STRING"},
                    "reason": {"type": "STRING"},
                },
                "required": ["concept", "title", "segment", "score", "criteria", "reason"],
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

    # 주제 — 하나씩 검증하고, 컨셉마다 점수 높은 순으로 다섯까지 남긴다.
    best: dict[tuple[str, int], dict] = {}
    below: list[dict] = []
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
        score = item.get("score")
        if not (isinstance(score, int | float) and not isinstance(score, bool) and 0 <= score <= 100):
            dropped.append({"title": title, "why": f"점수가 0~100 밖이다: {score!r}"})
            continue
        score = int(score)
        if score < MIN_SCORE:
            below.append({"concept": key, "title": title, "score": score})
            continue
        previous = best.get((key, idx))
        if previous is not None:
            dropped.append({"title": title, "why": f"같은 컨셉에 구간 {idx} 가 두 번"})
            if previous["score"] >= score:
                continue
        best[(key, idx)] = {
            "concept": key, "title": title,
            # 🔴 id 와 번호를 둘 다 남긴다. 번호는 프롬프트가 쓴 이름이고, id 는 구간을 다시 나눴는지 알아보는 열쇠다.
            "segment_id": by_idx[idx]["id"], "segment_idx": idx,
            "score": score,
            # 기준이 비면 가제로 채운다 — 카드로 만든 클립의 run 기준 문장(공개 탭에서 출처로 보인다)이 된다.
            "criteria": _text(item.get("criteria"), TEXT_MAX) or title,
            "reason": _text(item.get("reason"), TEXT_MAX) or None,
        }

    ideas: list[dict] = []
    for key in concepts.KEYS:
        # 점수가 같으면 모델이 낸 순서(dict 는 넣은 순서를 지킨다).
        ranked = sorted((i for i in best.values() if i["concept"] == key), key=lambda i: -i["score"])
        ideas.extend(ranked[:PER_CONCEPT])
        for extra in ranked[PER_CONCEPT:]:
            dropped.append({"title": extra["title"], "why": f"{key} 주제가 {PER_CONCEPT}개를 넘었다"})
    result = {
        "concepts": summary, "ideas": ideas, "dropped": len(dropped), "below": len(below), "min_score": MIN_SCORE,
    }
    return result, dropped + [{**b, "why": f"{MIN_SCORE}점 미만"} for b in below]


def run_for_source(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> int:
    """추천을 받아 `source_ideas` 에 덧붙인다. 그 행의 id 를 돌려준다. 실패하면 IdeaError(기록하고 올린다)."""
    source = conn.execute("select context from sources where id = %s", (source_id,)).fetchone()
    if source is None:
        raise IdeaError(f"source {source_id} 없음")
    every = ranking.load_segments(conn, source_id)
    # 🔴 클립이 이미 쓰는 구간은 입력에서 뺀다 — 추천 칸이 이미 만든 것으로 차지 않게. 구간 번호(idx)는 그대로라
    # 남은 구간끼리 번호가 띄엄띄엄이어도 된다(검증은 넘긴 목록으로 한다).
    taken = taken_segment_ids(conn, source_id)
    segments = [s for s in every if s["id"] not in taken]
    if every and not segments:
        raise IdeaError("모든 구간에 이미 클립이 있다 — 추천할 구간이 없다")
    # 관리자 기준(2층) — 어떤 숏폼을 만들지는 선호가 결과를 바꿔야 하는 자리다(standards.py "넣는다").
    prompt = build_prompt(segments, source["context"], standards.load(conn))
    # 🔴 모델 호출이 수십 초다 — 그동안 트랜잭션을 붙잡지 않는다.
    conn.commit()

    params = {"segments": len(segments), "skipped": len(every) - len(segments), "per_concept": PER_CONCEPT}
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


def taken_segment_ids(conn: psycopg.Connection, source_id: int) -> set[int]:
    """이 소스에서 클립이 쓰는 구간 id 전부 — 어느 길에서 나왔든.
    🔴 조합 클립(질문 경로)은 조각이 여러 구간에 걸친다. `clips.segment_id` 는 첫 조각뿐이라 `clip_parts` 도 본다."""
    rows = conn.execute(
        """select cl.segment_id as id from clips cl
             join runs r on r.id = cl.run_id where r.source_id = %s
           union
           select cp.segment_id from clip_parts cp
             join clips cl on cl.id = cp.clip_id
             join runs r on r.id = cl.run_id
            where r.source_id = %s and cp.segment_id is not null""",
        (source_id, source_id),
    ).fetchall()
    return {row["id"] for row in rows}


def clip_on_segment(conn: psycopg.Connection, segment_id: int) -> dict | None:
    """그 구간을 쓰는 클립 하나(먼저 만든 것). 없으면 None. 판단 기준은 `taken_segment_ids` 와 같다."""
    return conn.execute(
        """select cl.id, cl.title from clips cl
            where cl.segment_id = %s
               or exists (select 1 from clip_parts cp where cp.clip_id = cl.id and cp.segment_id = %s)
            order by cl.id limit 1""",
        (segment_id, segment_id),
    ).fetchone()


def find(conn: psycopg.Connection, source_id: int, idea_id: int, concept: str, segment_id: int) -> dict | None:
    """추천 행 하나에서 (컨셉, 구간) 으로 주제를 찾는다. 같은 컨셉에 같은 구간은 두 번 없다(`check`)."""
    row = conn.execute(
        "select result from source_ideas where id = %s and source_id = %s", (idea_id, source_id)
    ).fetchone()
    if row is None:
        return None
    for item in (row["result"] or {}).get("ideas") or []:
        if item.get("concept") == concept and item.get("segment_id") == segment_id:
            return item
    return None


def segment_exists(conn: psycopg.Connection, source_id: int, segment_id: int) -> bool:
    """구간을 다시 나누면 추천이 가리키던 id 가 사라진다(오래된 추천)."""
    return conn.execute(
        """select 1 from segments sg join chunks c on c.id = sg.chunk_id
            where sg.id = %s and c.source_id = %s""",
        (segment_id, source_id),
    ).fetchone() is not None


def cut(
    conn: psycopg.Connection, cfg: config.Config, source_id: int, idea_id: int, concept: str, segment_id: int,
) -> int:
    """추천 카드의 [클립 만들기]. 그 주제 하나짜리 run 을 만들고 구간을 자른다. 클립 id 를 돌려준다.

    run 의 컨셉 · 기준 문장은 추천의 것이다 — 자르기 · AI 카피가 컨셉을 읽고, 공개 탭이 기준 문장을 출처로 보인다.
    클립 제목의 기본값은 추천의 가제다(구간 설명 "…를 설명한다" 보다 숏폼 제목에 가깝다. 공개 탭에서 고친다).
    🔴 엔드포인트가 잡을 띄우기 전에 같은 검사를 한다. 여기서 다시 보는 이유는 CLI · 그 사이에 만든 클립이다.
    """
    item = find(conn, source_id, idea_id, concept, segment_id)
    if item is None:
        raise IdeaError(f"추천 {idea_id} 에 {concept} · 구간 {segment_id} 주제가 없다")
    if not segment_exists(conn, source_id, segment_id):
        raise IdeaError("구간을 다시 나눠 이 추천이 가리키던 구간이 없다 — 추천을 다시 받는다")
    taken = clip_on_segment(conn, segment_id)
    if taken is not None:
        raise IdeaError(f"이 구간을 쓰는 클립 {taken['id']} 이 이미 있다")

    run_id = conn.execute(
        """insert into runs (source_id, criteria_prompt, concept, idea_id, status)
           values (%s, %s, %s, %s, 'DONE') returning id""",
        (source_id, item.get("criteria") or item["title"], concept, idea_id),
    ).fetchone()["id"]
    # 🔴 자르기가 모델을 부른다 — run 을 먼저 커밋해 그 호출 기록(stage_calls.run_id)이 붙을 자리를 만든다.
    conn.commit()
    clip_id = cutting.run_for_segment(conn, cfg, run_id, segment_id, None)
    conn.execute("update clips set title = %s where id = %s", (item["title"], clip_id))
    conn.commit()
    return clip_id


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
        # 2026-11-14 전의 행에는 점수 · 문턱이 없다 — 화면이 점수 칸을 비운다.
        "below": result.get("below", 0), "minScore": result.get("min_score"),
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
