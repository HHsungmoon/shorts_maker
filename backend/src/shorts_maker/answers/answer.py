"""[답하기] — 질문 하나를 숏폼 하나로 (tease §2-2, update_plan M5).

**고정 DAG 다.** 다음에 뭘 할지 결정하는 주체가 없다 — 순서가 코드에 박혀 있다:

    run()   라우팅 + 구간 선택 → 후보 제안 → 예산 강제 → judge ×N (병렬) → **후보 저장, 멈춤**
    build() 크리에이터가 고른 후보 하나 → 컷 → 렌더

🔴 **왜 두 조각인가 (2026-09-09).** 예전에는 한 번에 끝까지 갔다 — 시스템이 승자를 골라 렌더까지
했다. 그러면 크리에이터에게는 클립 하나가 그냥 나온 것으로 보이고, 무엇과 겨뤘는지도 왜 그게
이겼는지도 알 수 없다. 판정 결과(자립 O/X·점수)만 보여주는 것도 답이 아니었다 —
**"조합, 2조각, 28초, 자립 X, 45점" 만 보고는 고를 수가 없다.** 사람은 내용을 읽어야 판단한다.
그래서 후보를 **대사 전문과 함께** 남기고 멈춘다. 판정은 사라지지 않고 **추천**이 된다.

`build()` 는 LLM 을 부르지 않는다 — 범위도 대사도 판정도 이미 있다. 그래서 마음을 바꿔
다른 후보를 골라도 추가 비용이 없고, 그게 사람을 고리에 넣을 수 있는 이유다.

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
    """**추천** 하나. 합격한 것 중 최고점, 하나도 합격 못 하면 그중 최고점.

    🔴 이건 결정이 아니라 추천이다(2026-09-09). 예전에는 이 함수가 고른 것이 곧 클립이 됐지만,
    지금은 화면에서 "추천" 표시가 될 뿐이고 무엇을 만들지는 크리에이터가 고른다.

    🔴 전부 떨어져도 버리지 않는다. 점수가 낮아도 사람이 읽어 보고 쓸 만하다고 판단할 수 있다 —
    무한 재시도보다 사람의 눈이 낫다.
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


def _suggest(
    conn: psycopg.Connection, cfg: config.Config, cluster: dict, segments: list[dict],
    run_id: int | None = None,
) -> int | None:
    """다른 편 안내 대상. 🔴 **실패해도 답변 불가 처리를 막지 않는다.**

    안내는 부수 정보다. 임베딩 할당량이 떨어졌다고 "답할 구간 없음" 을 기록조차 못 하면
    클러스터가 IN_PROGRESS 에 갇혀 크리에이터가 다시 누를 수도 없게 된다.
    """
    try:
        return retrieval.suggest_elsewhere(conn, cfg, cluster, segments, run_id)
    except Exception:
        conn.rollback()
        return None


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
    everything = retrieval.load_segments(conn, source_id)
    if not everything:
        return _unanswerable(conn, cluster["id"], run_id, "구간이 없습니다", None)

    # ① 라우팅 + 구간 선택 — **LLM 1회로 둘 다.** 왜 합쳤는지는 routing 모듈 머리 주석에 있다.
    decision = routing.decide(conn, cfg, question, everything, source_id, run_id)
    conn.execute("update runs set route = %s where id = %s", (decision.route, run_id))
    # 🔴 여기서 끊는다. 방금 돈을 쓴 LLM 호출의 기록(stage_calls)과 경로를 먼저 굳혀야 한다 —
    # 아래에서 실패해 롤백하면 그 기록까지 사라진다.
    conn.commit()

    # ② 다음 단계에 대사를 넘길 구간을 정한다
    if decision.route == routing.RANK:
        # 찾을 대상이 없는 요청이다 — 전 구간을 놓고 고른다(기존 크리에이터 경로).
        segments = everything
    elif decision.selected is None:
        # 🔴 판정을 못 읽었다. 임베딩 top-k 로 물러난다 — 실측에서 약한 신호로 드러났지만,
        # 아무것도 없이 포기하는 것보다는 낫다. 다음 단계가 한 번 더 거른다.
        found = retrieval.candidates(conn, cfg, cluster, run_id=run_id)
        segments = found.segments
        if not segments or found.best_score < cfg.retrieval_min_sim:
            return _unanswerable(
                conn, cluster["id"], run_id,
                f"이 영상에서 관련된 부분을 찾지 못했습니다 (최대 유사도 {found.best_score:.2f})",
                _suggest(conn, cfg, cluster, everything, run_id),
            )
    elif not decision.selected:
        # 모델이 구간 목록 전체를 보고 "이 영상엔 없다" 고 답했다. 옛 검출기
        # (`SHORTS_RETRIEVAL_MIN_SIM`)가 하려던 일인데 그건 실측에서 한 번도 걸러내지 못했다.
        return _unanswerable(
            conn, cluster["id"], run_id,
            decision.reason or "이 영상에서 관련된 부분을 찾지 못했습니다",
            _suggest(conn, cfg, cluster, everything, run_id),
        )
    else:
        # 🔴 **시간 순으로 정렬한다.** 모델은 관련 높은 순으로 주는데, 다음 단계의 조합 후보는
        # 조각이 시간 순이어야 하고(`ranking.parse_answer`) 발화 번호도 시간 순으로 매겨진다.
        chosen = set(decision.selected)
        segments = [s for s in everything if s["idx"] in chosen]
    conn.commit()

    # ③ 후보 3개 제안 (LLM 1회)
    plan, lines = ranking.plan_answer(
        conn, cfg, question, segments, source["context"], source_id=source_id, run_id=run_id
    )
    if not plan.answerable:
        return _unanswerable(
            conn, cluster["id"], run_id, plan.reason, _suggest(conn, cfg, cluster, everything, run_id)
        )

    # ④ 시작점 보정 + 예산 강제 — 🔴 LLM 이 말한 길이도, 고른 시작점도 그대로 믿지 않는다
    prepared: list[tuple[object, list]] = []
    shapes: set[tuple] = set()
    for candidate in plan.candidates:
        # 🔴 앞을 가리키며 시작하면 시작점을 당긴다. 프롬프트에 "지시대명사로 시작하지 않는다" 가
        # 있는데도 실측에서 후보 셋이 전부 그렇게 시작했다(cutting.LEAD_IN_MARKERS 주석).
        # **예산 강제보다 먼저** 한다 — 늘린 만큼 뒤에서 잘라내야 총량이 맞는다.
        ranges = cutting.lead_in(candidate.parts, lines, cfg.teaser_max_sec)
        parts = cutting.enforce_budget(
            cutting.resolve_parts(ranges, lines), cfg.teaser_max_sec, lines
        )
        # 🔴 **같아진 후보는 버린다.** 시작점 보정과 예산 강제를 거치고 나면 서로 다르게 나온
        # 후보가 같은 범위로 수렴할 수 있다(실측: tight 를 당겼더니 single 과 똑같아졌다).
        # 크리에이터에게 똑같은 카드를 둘 보여주는 것은 고를 것을 주는 게 아니고, judge 호출도
        # 하나 더 쓴다 — 무료 등급에서는 그게 곧 하루에 몇 번 답할 수 있느냐다.
        shape = tuple((p.chunk_id, p.start_utterance_idx, p.end_utterance_idx) for p in parts)
        if shape in shapes:
            continue
        shapes.add(shape)
        prepared.append((candidate, parts))
    conn.commit()

    # ⑤ judge 병렬. 🔴 judge 는 DB 를 만지지 않는다 — 그래서 스레드로 돌릴 수 있다.
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS, thread_name_prefix="judge") as pool:
        results = list(pool.map(
            lambda item: judge.judge(cfg, question, [p.text for p in item[1]]), prepared
        ))
    total_ms = int((time.monotonic() - started) * 1000)
    for (candidate, parts), judged in zip(prepared, results):
        usage = judged.usage
        conn.execute(
            """insert into stage_calls
               (source_id, run_id, stage, model, input_tokens, output_tokens, thinking_tokens,
                total_tokens, cached_tokens, latency_ms, prompt, response, params)
               values (%s, %s, 'judge', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                source_id, run_id, cfg.gemini_model, usage["input_tokens"], usage["output_tokens"],
                usage["thinking_tokens"], usage["total_tokens"], usage["cached_tokens"],
                judged.latency_ms,
                # 🔴 후보 셋 전부의 본문이 남는다. 떨어진 둘은 클립이 되지 않으므로 여기 없으면
                # 어디에도 없다 — "왜 그게 아니라 이게 이겼나" 를 나중에 답할 수 없게 된다.
                judged.prompt if cfg.store_prompts else None,
                judged.raw if cfg.store_prompts else None,
                Jsonb({
                    "label": candidate.label, "parts": len(parts),
                    "total_sec": round(sum(p.length for p in parts), 2),
                    "standalone": judged.verdict.standalone, "answers": judged.verdict.answers,
                    "score": judged.verdict.score,
                }),
            ),
        )
    scored = [
        Scored(candidate, parts, judged.verdict)
        for (candidate, parts), judged in zip(prepared, results)
    ]
    recommended = _pick(scored)

    # ⑥ 후보를 **대사와 함께** 남기고 멈춘다. 🔴 여기서 컷·렌더를 하지 않는다 —
    # 무엇을 숏폼으로 만들지는 크리에이터가 고른다(`build`). 판정은 사라지지 않고 **추천**이 된다.
    conn.execute("delete from run_candidates where run_id = %s", (run_id,))
    saved: list[dict] = []
    for ordinal, item in enumerate(scored):
        row = conn.execute(
            """insert into run_candidates (run_id, ordinal, label, reason, parts, total_sec,
                                           standalone, answers, score, judge_note)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (
                run_id, ordinal, item.candidate.label, item.candidate.reason,
                Jsonb(_parts_json(item.parts)),
                round(sum(p.length for p in item.parts), 3),
                item.verdict.standalone, item.verdict.answers, item.verdict.score,
                item.verdict.reason,
            ),
        ).fetchone()
        saved.append({"id": row["id"], "label": item.candidate.label,
                      "recommended": item is recommended})

    conn.execute(
        "update runs set status = 'DONE', ranked = %s, updated_at = now() where id = %s",
        (
            Jsonb({
                "answerable": True,
                "reason": plan.reason,
                "route": decision.route,
                "candidates": len(scored),
                "judgeMs": total_ms,
            }),
            run_id,
        ),
    )
    # 🔴 REVIEW 는 "사람 차례" 라는 뜻이다. 예전에는 클립이 이미 있는 상태였고, 지금은 고를
    # 후보가 있는 상태다. 화면이 클립 유무로 갈라 그린다(ClusterPanel).
    clusters.transition(conn, cluster["id"], "REVIEW")
    conn.commit()
    return {
        "answerable": True,
        "runId": run_id,
        "route": decision.route,
        "candidates": saved,
        "judgeMs": total_ms,
    }


def _parts_json(parts: list) -> list[dict]:
    """조각을 저장 형태로. 🔴 `text` 를 반드시 넣는다 — 크리에이터가 읽고 고르는 것이 그것이다."""
    return [
        {
            "ordinal": ordinal,
            "segment_id": part.segment_id,
            "chunk_id": part.chunk_id,
            "start_sec": part.start_sec,
            "end_sec": part.end_sec,
            "start_utterance_idx": part.start_utterance_idx,
            "end_utterance_idx": part.end_utterance_idx,
            "text": part.text,
        }
        for ordinal, part in enumerate(parts)
    ]


def _parts_from_json(rows: list[dict]) -> list[cutting.PartSpec]:
    return [
        cutting.PartSpec(
            segment_id=row["segment_id"], chunk_id=row["chunk_id"],
            start_utterance_idx=row["start_utterance_idx"],
            end_utterance_idx=row["end_utterance_idx"],
            start_sec=float(row["start_sec"]), end_sec=float(row["end_sec"]),
            text=row["text"],
        )
        for row in rows
    ]


def build(conn: psycopg.Connection, cfg: config.Config, candidate_id: int) -> dict:
    """크리에이터가 고른 후보 하나를 **숏폼으로 만든다** — 컷 + 렌더.

    🔴 **LLM 을 부르지 않는다.** 범위도 대사도 판정도 이미 있다. 여기서 드는 건 ffmpeg 시간뿐이다.
    그래서 크리에이터가 마음을 바꿔 다른 후보를 골라도 추가 비용이 없다.

    다시 고르면 그 run 의 기존 클립을 **지우고** 새로 만든다. `uq_clips_run_segment` 때문이기도
    하지만, 무엇보다 한 질문에 답하는 클립은 하나여야 한다(불변식 I1).
    """
    row = conn.execute(
        """select rc.*, r.source_id, r.criteria_prompt, qc.id as cluster_id, qc.status
           from run_candidates rc
           join runs r on r.id = rc.run_id
           left join question_clusters qc on qc.run_id = r.id
           where rc.id = %s""",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise AnswerError(f"candidate {candidate_id} 없음")
    candidate = dict(row)
    parts = _parts_from_json(candidate["parts"])
    if not parts:
        raise AnswerError("조각이 없는 후보다")

    # 이전에 고른 것이 있으면 치운다. 파일은 남지만 DB 에서 떨어져 나가고 렌더가 새로 돈다.
    conn.execute("delete from clips where run_id = %s", (candidate["run_id"],))
    conn.execute(
        "update run_candidates set chosen_at = null where run_id = %s", (candidate["run_id"],)
    )

    clip_id = cutting.create_answer_clip(
        conn, candidate["run_id"], parts, candidate["score"], candidate["reason"] or "",
        # 질문이 곧 이 숏폼의 제목이다 — 시청자가 목록에서 보는 것도 이 문장이다.
        title=candidate["criteria_prompt"],
    )
    if candidate["cluster_id"] is not None:
        conn.execute(
            "update clips set question_cluster_id = %s where id = %s",
            (candidate["cluster_id"], clip_id),
        )
    conn.execute(
        "update run_candidates set chosen_at = now() where id = %s", (candidate_id,)
    )
    # judge 소견을 클립에 남긴다. 사람의 OK/NG 와 나란히 쌓여 둘의 일치율이 품질 지표가 된다(§11).
    passed = bool(candidate["standalone"]) and bool(candidate["answers"])
    conn.execute(
        "insert into clip_reviews (clip_id, verdict, note, reviewer) values (%s, %s, %s, 'llm')",
        (clip_id, "OK" if passed else "NG", candidate["judge_note"]),
    )
    conn.commit()

    # 🔴 조각이 여럿이면 브릿지 카드를 끼워 **한 번의 인코딩**으로 이어붙인다.
    render.run_for_clip(conn, cfg, clip_id, force=True)
    conn.commit()
    return {
        "clipId": clip_id,
        "runId": candidate["run_id"],
        "label": candidate["label"],
        "parts": len(parts),
        "totalSec": round(sum(p.length for p in parts), 2),
    }
