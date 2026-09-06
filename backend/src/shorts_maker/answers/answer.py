"""[답하기] — 질문 하나를 숏폼 하나로 (tease §2-2, update_plan M5).

**고정 DAG 다.** 다음에 뭘 할지 결정하는 주체가 없다 — 순서가 코드에 박혀 있다:

    라우팅 → (검색) → 후보 3개 제안 → 예산 강제 → judge ×3 (병렬) → 승자 선택 → 컷 → 렌더

⚠️ 그래서 이건 "에이전트 하네스" 가 아니다. **경계가 있는 판정 하나**다. 제출 문서에도 그렇게
쓴다 — 정확한 용어가 심사에서 더 강하다.

🔴 **best-of-3 인 이유.** 질문 하나에 답하는 방식이 여럿이다 — 직접 답하는 한 덩어리, 흩어진 답을
이어붙인 조합, 핵심만 짧게. 어느 게 나은지는 잘라놓은 **대사를 읽어봐야** 안다. rank 한 번으로
셋을 받아두면 judge 를 병렬로 돌려 체감 시간이 한 번과 같고, 고를 것이 실제로 여러 개다.
원래 계획은 "하나 만들고 떨어지면 줄여서 재시도" 였는데, 순차라 느리고 두 번째가 첫 번째보다
낫다는 보장도 없었다.

🔴 클러스터 상태는 `clusters.transition()` 으로만 바꾼다. 실패하면 OPEN 으로 되돌려 크리에이터가
다시 누를 수 있게 한다 — 이유는 `runs.error` 에 남는다.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..pipeline import cutting, ranking, render
from . import clusters, judge, retrieval, routing

# judge 를 동시에 몇 개까지. 🔴 "워커 1개" 규약(jobs.py)과 충돌하지 않는다 — 그 규약은 CPU 를
# 다 먹는 영상 처리·STT 가 겹치지 말라는 것이고, 이건 짧은 HTTP 요청 셋이다.
JUDGE_WORKERS = 3


class AnswerError(RuntimeError):
    pass


@dataclass
class Scored:
    candidate: object
    parts: list
    verdict: judge.Verdict


def _pick(scored: list[Scored]) -> Scored:
    """합격한 것 중 최고점. 하나도 합격하지 못하면 그중 최고점을 올리고 경고를 남긴다.

    🔴 전부 떨어졌다고 버리지 않는다. 크리에이터가 보고 판단할 수 있게 REVIEW 로 올리고 judge 의
    소견을 붙인다 — 무한 재시도보다 사람의 눈이 낫다.
    """
    passed = [s for s in scored if s.verdict.passed]
    return max(passed or scored, key=lambda s: s.verdict.score)


def run(conn: psycopg.Connection, cfg: config.Config, cluster_id: int) -> dict:
    cluster = conn.execute(
        "select * from question_clusters where id = %s", (cluster_id,)
    ).fetchone()
    if cluster is None:
        raise AnswerError(f"cluster {cluster_id} 없음")
    cluster = dict(cluster)
    source = conn.execute(
        "select id, context, channel from sources where id = %s", (cluster["source_id"],)
    ).fetchone()
    question = cluster["canonical_text"]

    clusters.transition(conn, cluster_id, "IN_PROGRESS")
    run_id = conn.execute(
        """insert into runs (source_id, criteria_prompt, status) values (%s, %s, 'RUNNING')
           returning id""",
        (cluster["source_id"], question),
    ).fetchone()["id"]
    conn.execute(
        "update question_clusters set run_id = %s where id = %s", (run_id, cluster_id)
    )
    conn.commit()

    try:
        return _run_inside(conn, cfg, cluster, source, run_id, question)
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "update runs set status = 'FAILED', error = %s, updated_at = now() where id = %s",
            (f"{type(exc).__name__}: {exc}"[:1000], run_id),
        )
        # 🔴 OPEN 으로 되돌린다. IN_PROGRESS 로 두면 크리에이터가 다시 누를 수 없다.
        clusters.transition(conn, cluster_id, "OPEN")
        conn.commit()
        raise


def _unanswerable(
    conn: psycopg.Connection, cluster_id: int, run_id: int, reason: str, suggested: int | None
) -> dict:
    conn.execute(
        "update runs set status = 'DONE', ranked = %s, updated_at = now() where id = %s",
        (Jsonb({"answerable": False, "reason": reason}), run_id),
    )
    clusters.transition(conn, cluster_id, "UNANSWERABLE", suggested_source_id=suggested)
    conn.commit()
    return {"answerable": False, "reason": reason, "suggestedSourceId": suggested, "runId": run_id}


def _run_inside(
    conn: psycopg.Connection, cfg: config.Config, cluster: dict, source, run_id: int, question: str
) -> dict:
    source_id = cluster["source_id"]

    # ① 라우팅 — 찾을 대상이 있는 질문인가, 아니면 전체에서 고르라는 요청인가
    route = routing.classify(conn, cfg, question, source_id)
    conn.execute("update runs set route = %s where id = %s", (route, run_id))

    # ② 후보 구간 좁히기
    suggested: int | None = None
    if route == routing.RETRIEVAL:
        found = retrieval.candidates(conn, cfg, cluster)
        segments = found.segments
        # 다른 영상이 더 가까우면 안내용으로 기억해 둔다(클립은 이 영상에서만 만든다).
        if found.elsewhere_source_id and found.elsewhere_score > found.best_score:
            suggested = found.elsewhere_source_id
        if not segments or found.best_score < cfg.retrieval_min_sim:
            return _unanswerable(
                conn, cluster["id"], run_id,
                f"이 영상에서 관련된 부분을 찾지 못했습니다 (최대 유사도 {found.best_score:.2f})",
                suggested,
            )
    else:
        segments = retrieval.load_segments(conn, source_id)
        if not segments:
            return _unanswerable(conn, cluster["id"], run_id, "구간이 없습니다", None)
    conn.commit()

    # ③ 후보 3개 제안 (LLM 1회)
    plan, lines = ranking.plan_answer(
        conn, cfg, question, segments, source["context"], source_id=source_id, run_id=run_id
    )
    if not plan.answerable:
        return _unanswerable(conn, cluster["id"], run_id, plan.reason, suggested)

    # ④ 예산 강제 — 🔴 LLM 이 말한 길이는 믿지 않는다
    prepared: list[tuple[object, list]] = []
    for candidate in plan.candidates:
        parts = cutting.enforce_budget(
            cutting.resolve_parts(candidate.parts, lines), cfg.teaser_max_sec, lines
        )
        prepared.append((candidate, parts))
    conn.commit()

    # ⑤ judge 병렬. 🔴 judge 는 DB 를 만지지 않는다 — 그래서 스레드로 돌릴 수 있다.
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS, thread_name_prefix="judge") as pool:
        results = list(pool.map(
            lambda item: judge.judge(cfg, question, [p.text for p in item[1]]), prepared
        ))
    total_ms = int((time.monotonic() - started) * 1000)
    for (candidate, parts), (verdict, usage, latency_ms) in zip(prepared, results):
        conn.execute(
            """insert into stage_calls
               (source_id, run_id, stage, model, input_tokens, output_tokens, thinking_tokens,
                total_tokens, cached_tokens, latency_ms, params)
               values (%s, %s, 'judge', %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                source_id, run_id, cfg.gemini_model, usage["input_tokens"], usage["output_tokens"],
                usage["thinking_tokens"], usage["total_tokens"], usage["cached_tokens"], latency_ms,
                Jsonb({
                    "label": candidate.label, "parts": len(parts),
                    "total_sec": round(sum(p.length for p in parts), 2),
                    "standalone": verdict.standalone, "answers": verdict.answers,
                    "score": verdict.score,
                }),
            ),
        )
    scored = [
        Scored(candidate, parts, verdict)
        for (candidate, parts), (verdict, _, _) in zip(prepared, results)
    ]
    winner = _pick(scored)

    # ⑥ 컷 — rank 가 범위를 이미 정했으므로 LLM 을 다시 부르지 않는다
    clip_id = cutting.create_answer_clip(
        conn, run_id, winner.parts, float(winner.verdict.score), winner.candidate.reason
    )
    conn.execute(
        "update clips set question_cluster_id = %s where id = %s", (cluster["id"], clip_id)
    )
    conn.execute(
        "update runs set status = 'DONE', ranked = %s, updated_at = now() where id = %s",
        (
            Jsonb({
                "answerable": True,
                "reason": plan.reason,
                "route": route,
                "candidates": [
                    {
                        "label": s.candidate.label,
                        "parts": [
                            {"start_sec": p.start_sec, "end_sec": p.end_sec} for p in s.parts
                        ],
                        "total_sec": round(sum(p.length for p in s.parts), 2),
                        "standalone": s.verdict.standalone,
                        "answers": s.verdict.answers,
                        "score": s.verdict.score,
                        "reason": s.verdict.reason,
                        "won": s is winner,
                    }
                    for s in scored
                ],
                "judgeMs": total_ms,
            }),
            run_id,
        ),
    )
    # judge 소견을 클립에 남긴다. 사람의 OK/NG 와 나란히 쌓여 둘의 일치율이 품질 지표가 된다(§11).
    conn.execute(
        """insert into clip_reviews (clip_id, verdict, note, reviewer)
           values (%s, %s, %s, 'llm')""",
        (clip_id, "OK" if winner.verdict.passed else "NG", winner.verdict.reason),
    )
    conn.commit()

    # ⑦ 렌더 — 조각이 여럿이면 브릿지 카드를 끼워 이어붙인다
    out = render.run_for_clip(conn, cfg, clip_id, force=True)
    clusters.transition(conn, cluster["id"], "REVIEW")
    conn.commit()
    return {
        "answerable": True,
        "runId": run_id,
        "clipId": clip_id,
        "route": route,
        "label": winner.candidate.label,
        "parts": len(winner.parts),
        "totalSec": round(sum(p.length for p in winner.parts), 2),
        "verdict": {
            "standalone": winner.verdict.standalone,
            "answers": winner.verdict.answers,
            "score": winner.verdict.score,
            "reason": winner.verdict.reason,
        },
        "path": str(out),
    }
