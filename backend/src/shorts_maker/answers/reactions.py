"""시청자 반응 — 감상 댓글 AI 요약 (tease §3-6, 2026-11-01).

댓글 가져오기(`answers/comments.py`)가 감상으로 가른 댓글을 Gemini 에 **한 번** 보내 묶음별 요약을 받는다.
리포트 탭의 "시청자 반응" 이 원문 목록 위에 보인다.

🔴 사람이 누를 때만 돈다(잡, 워커 1개). 감상이 0개면 부르지 않는다. 결과는 `reaction_summaries` 에 쌓고
리포트는 최신 것을 보인다 — 댓글을 다시 가져와 감상 수가 바뀌어도 **자동으로 다시 부르지 않는다**(화면이 알린다).

🔴 **"몇 명이 이렇게 말했다" 는 코드가 센다.** 모델은 묶음마다 근거 댓글 **번호**만 낸다. 번호는 화이트리스트로
검증하고, 개수는 남은 번호의 길이다 — 모델이 말한 숫자를 쓰지 않는다(질문 묶기의 "개수는 SQL 이" 와 같다).
근거가 하나도 안 남은 묶음은 버린다 — 근거 없는 요약은 지어낸 것과 구별이 안 된다.

프롬프트 층(standards.py 머리 주석):
  - 1층 규칙(출력 형식 · 근거 번호 · 지어내지 않기)은 고정이다.
  - 🔴 관리자 기준(2층)은 **넣지 않는다.** 그건 클립 선호이고, 여기는 리뷰를 읽는 관점이라 다른 글이다.
    대신 **요약 관점**(`standards.REACTION`)을 프롬프트 화면에서 고친다.
  - 영상 개요(3층)는 넣는다 — 무슨 영상에 대한 반응인지 알아야 "아쉬운 점" 을 제대로 읽는다.
"""

import json
import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config, standards
from ..adapters import gemini
from . import comments

# 한 번에 보내는 감상 상한. 원티드 영상 댓글은 편당 2~29개라(2026-10-31) 닿을 일이 드물다. 넘으면 **최근 것**을
# 남긴다 — 리포트는 지금 회차가 어떻게 받아들여지는지를 말하는 자리다. 몇 개를 뺐는지는 결과에 남는다.
MAX_COMMENTS = 300

# 한 댓글을 프롬프트에 넣을 때의 상한. 감상은 500자를 넘는 것도 있다(사연). 앞부분이면 무엇에 대한 말인지 보인다.
COMMENT_MAX_CHARS = 600

# 묶음 종류. 화면이 이 순서로 보인다.
KINDS = ("positive", "concern", "request")
KIND_LABEL = {"positive": "좋았던 점", "concern": "아쉬운 점", "request": "바라는 것"}

# 묶음 상한. 이보다 많으면 요약이 아니라 목록이다.
MAX_THEMES = 8

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "overview": {"type": "STRING"},
        "themes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": list(KINDS)},
                    "title": {"type": "STRING"},
                    "detail": {"type": "STRING"},
                    "comments": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                },
                "required": ["kind", "title", "detail", "comments"],
            },
        },
    },
    "required": ["overview", "themes"],
}

# 🔴 1층 규칙. 프롬프트 화면에 원문 그대로 보인다(http/prompt_catalog.py) — 칸 이름을 바꾸면 그쪽 LAYER_OF 도 고친다.
PROMPT = """아래는 한 영상에 시청자들이 남긴 감상 댓글이다. 질문 댓글은 이미 뺐다.
이 영상이 **어떻게 받아들여졌는지** 영상을 만든 사람이 한눈에 보도록 요약하라.

{context_block}{guide_block}규칙:
- overview: 전체 반응을 두세 문장으로. 댓글에 없는 말을 보태지 않는다.
- themes: 비슷한 말을 한 댓글을 묶는다. 묶음마다
  - kind: positive(좋았던 점) · concern(아쉬운 점) · request(바라는 것 — 다음 영상·후속 내용 요청) 중 하나
  - title: 한 줄 제목
  - detail: 무엇을 어떻게 말했는지 한두 문장. 댓글을 그대로 길게 옮기지 않는다
  - comments: 그 묶음의 근거가 되는 댓글의 [번호]. 반드시 아래 목록에 있는 번호만 쓴다
- 묶음은 많아야 {limit}개. 한 댓글만 말한 것도 뚜렷하면 묶음이 될 수 있다.
- 몇 명이 말했는지는 쓰지 않는다 — 근거 번호로 따로 센다.
- 고마움·응원 같은 인사만 있는 댓글은 positive 하나로 모아도 된다.

댓글:
{comments}
"""


class ReactionError(RuntimeError):
    """사람이 고칠 수 있는 이유로 요약할 수 없다. 메시지를 그대로 화면에 보인다."""


def build_prompt(reviews: list[dict], context: str | None, guide: str | None) -> str:
    """번호는 목록 안 순서(1부터)다. 🔴 DB id 를 보이지 않는다 — 큰 숫자는 모델이 옮겨 적다 틀린다."""
    lines = []
    for n, review in enumerate(reviews, start=1):
        text = review["text"]
        if len(text) > COMMENT_MAX_CHARS:
            text = text[:COMMENT_MAX_CHARS].rstrip() + "…"
        lines.append(f"[{n}] {text}")
    context_block = f"영상 개요: {context.strip()}\n\n" if context and context.strip() else ""
    # 🔴 비면 줄 자체를 뺀다(standards.block 과 같은 규칙) — 빈 지시문을 남기면 모델이 그걸 해석한다.
    guide_block = (
        f"요약 관점 — 이 채널이 반응에서 특히 보고 싶은 것이다. 위 규칙과 출력 형식은 그대로 지킨다:\n{guide.strip()}\n\n"
        if guide and guide.strip() else ""
    )
    return PROMPT.format(
        context_block=context_block, guide_block=guide_block, limit=MAX_THEMES, comments="\n".join(lines)
    )


def parse(raw: str, reviews: list[dict]) -> dict:
    """모델 응답을 검증해 저장할 모양으로. 근거는 댓글 id 로 바꾼다(번호는 이번 프롬프트 안에서만 뜻이 있다).

    🔴 번호 화이트리스트 · 묶음 안 중복 제거 · 근거 없는 묶음 버림 · 모르는 kind 버림 · 묶음 상한.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReactionError(f"요약 응답을 읽지 못했다: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReactionError("요약 응답이 객체가 아니다")
    overview = str(payload.get("overview") or "").strip()
    themes = []
    for item in payload.get("themes") or []:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        ids: list[int] = []
        for number in item.get("comments") or []:
            # 🔴 bool 은 int 의 하위형이다 — True 를 1번으로 읽지 않는다.
            if isinstance(number, bool) or not isinstance(number, int):
                continue
            if 1 <= number <= len(reviews):
                comment_id = reviews[number - 1]["id"]
                if comment_id not in ids:
                    ids.append(comment_id)
        title = str(item.get("title") or "").strip()
        if not ids or not title:
            continue
        themes.append({
            "kind": item["kind"], "title": title, "detail": str(item.get("detail") or "").strip(), "comments": ids,
        })
        if len(themes) == MAX_THEMES:
            break
    if not overview and not themes:
        raise ReactionError("요약이 비어 있다")
    # 화면 순서: 좋았던 점 → 아쉬운 점 → 바라는 것. 같은 종류 안에서는 근거가 많은 것부터.
    themes.sort(key=lambda t: (KINDS.index(t["kind"]), -len(t["comments"])))
    return {"overview": overview, "themes": themes}


def summarize(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> dict:
    """[AI 요약] — 감상 댓글을 한 번 요약해 쌓는다."""
    source = conn.execute("select context from sources where id = %s", (source_id,)).fetchone()
    if source is None:
        raise ReactionError(f"source {source_id} 없음")
    reviews = comments.reviews_of(conn, source_id)
    if not reviews:
        raise ReactionError("감상 댓글이 없습니다. 질문 탭에서 [댓글에서 질문 가져오기] 를 먼저 눌러 주세요")
    total = len(reviews)
    sent = reviews[-MAX_COMMENTS:]
    guide = standards.load(conn, standards.REACTION)
    prompt = build_prompt(sent, source["context"], guide)
    # 🔴 몇 초 도는 호출 앞에서 트랜잭션을 끊는다(규약).
    conn.commit()

    params = {"reviews": total, "sent": len(sent), "guide": guide is not None}
    try:
        raw, usage, latency_ms = gemini.generate_json(cfg, prompt, SCHEMA)
    except Exception as exc:
        conn.execute(
            "insert into stage_calls (source_id, stage, model, params, error) values (%s, 'reaction', %s, %s, %s)",
            (source_id, cfg.gemini_model, Jsonb(params), f"{type(exc).__name__}: {exc}"),
        )
        conn.commit()
        raise
    error = None
    try:
        body = parse(raw, sent)
    except ReactionError as exc:
        error = f"ReactionError: {exc}"
        body = None
    if body is not None:
        params["themes"] = len(body["themes"])
    # 🔴 성공도 실패도 남긴다(규약: 모든 단계).
    conn.execute(
        """insert into stage_calls (source_id, stage, model, input_tokens, output_tokens,
                                    thinking_tokens, total_tokens, cached_tokens, latency_ms,
                                    prompt, response, params, error)
           values (%s, 'reaction', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            source_id, cfg.gemini_model, usage["input_tokens"], usage["output_tokens"],
            usage["thinking_tokens"], usage["total_tokens"], usage["cached_tokens"], latency_ms,
            prompt if cfg.store_prompts else None,
            raw if cfg.store_prompts else None,
            Jsonb(params), error,
        ),
    )
    if body is None:
        conn.commit()
        raise ReactionError(error.removeprefix("ReactionError: "))
    row = conn.execute(
        """insert into reaction_summaries (source_id, reviews, body, guide)
           values (%s, %s, %s, %s) returning id, created_at""",
        (source_id, total, Jsonb(body), guide),
    ).fetchone()
    conn.commit()
    return {"summaryId": row["id"], "reviews": total, "sent": len(sent), "themes": len(body["themes"])}


def latest(conn: psycopg.Connection, source_id: int) -> dict | None:
    row = conn.execute(
        """select id, reviews, body, guide, created_at from reaction_summaries
            where source_id = %s order by id desc limit 1""",
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


def view(conn: psycopg.Connection, source_id: int) -> dict:
    """리포트의 "시청자 반응" 덩어리 — 개수 · 감상 원문 전부 · 최신 요약."""
    counted = comments.counts(conn, source_id)
    reviews = comments.reviews_of(conn, source_id)
    summary = latest(conn, source_id)
    shown = None
    if summary is not None:
        # 근거가 그 뒤 다시 가져오기로 사라졌으면(삭제된 댓글 · 질문으로 갈래가 바뀐 것) 목록에서 뺀다.
        alive = {r["id"] for r in reviews}
        themes = []
        for theme in summary["body"].get("themes", []):
            ids = [i for i in theme["comments"] if i in alive]
            if ids:
                themes.append({**theme, "label": KIND_LABEL[theme["kind"]], "comments": ids, "count": len(ids)})
        shown = {
            "id": summary["id"],
            "overview": summary["body"].get("overview", ""),
            "themes": themes,
            "reviews": summary["reviews"],
            "createdAt": summary["created_at"],
            # 요약할 때와 감상 수가 다르면 화면이 "요약 뒤 댓글이 바뀌었다" 를 띄운다.
            "stale": summary["reviews"] != len(reviews),
        }
    return {
        "total": counted["total"],
        "questions": counted["questions"],
        "reviews": [{"id": r["id"], "text": r["text"], "publishedAt": r["published_at"]} for r in reviews],
        "fetchedAt": counted["fetched_at"],
        "summary": shown,
    }
