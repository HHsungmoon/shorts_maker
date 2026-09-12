"""회차 리포트 — 이 설명회가 답한 것과 답하지 않은 것 (기획서 §05-③ Type C).

🔴 **이건 대시보드가 아니라 산출물이다.** 크리에이터가 회차마다 손에 쥐고, 다음 설명회의
큐시트로 쓰고, 공고에서 빠진 정보를 채우는 데 쓴다. 그래서 화면에서 읽는 것만큼이나
**복사해서 가져갈 수 있는 것**이 중요하다.

세 덩어리다.

  ① **이 영상이 답하지 않은 질문** — 수요 순. 판정이 남긴 **이유**를 함께 준다.
     "답할 구간 없음" 딱지만으로는 영상이 정말 안 다룬 건지 검색이 빗나간 건지 알 수 없다.
  ② **답한 질문** — 무엇이 숏폼이 됐는지. 발행 여부까지.
  ③ **비용** — 🔴 **회차 준비와 질문 답변을 가른다.** 전사·분할은 영상당 한 번이고
     답변은 질문마다다. 하나로 합치면 "질문 하나에 얼마" 라는 숫자가 나오지 않는다.

🔴 **기회손실 추정치는 넣지 않는다**(기획서 §07 "예상 이탈 지원자 약 18명"). 전환 표본이
0이라 근거가 없다. 근거 없는 숫자는 리포트의 나머지 숫자까지 의심받게 만든다.
"""

import psycopg

from .. import config, pricing
from . import clusters

# 답하지 않은 것으로 세는 상태. 🔴 셋의 뜻이 다르므로 따로 담아 화면이 구분해 그린다.
MISSING = "UNANSWERABLE"   # 찾아봤는데 이 영상에 없다
WAITING = "OPEN"           # 아직 [답하기] 를 안 눌렀다
DECLINED = "DECLINED"      # 만들었는데 크리에이터가 물렸다


def _questions(conn: psycopg.Connection, cluster_id: int) -> list[str]:
    return [q["text"] for q in clusters.questions_of(conn, cluster_id)]


def build(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> dict:
    """이 영상의 회차 리포트."""
    source = conn.execute(
        "select id, title, channel, duration_sec, published from sources where id = %s",
        (source_id,),
    ).fetchone()
    if source is None:
        raise ValueError(f"source {source_id} 없음")

    found = clusters.demand(conn, source_id)
    by_status: dict[str, list[dict]] = {}
    for cluster in found:
        by_status.setdefault(cluster["status"], []).append(cluster)

    def rows(status: str) -> list[dict]:
        out = []
        for cluster in by_status.get(status, []):
            out.append({
                "clusterId": cluster["id"],
                "question": cluster["canonical_text"],
                "askedBy": cluster["question_count"],
                "likes": cluster["like_count"],
                # 🔴 왜 답하지 못했는지. 이게 리포트의 값이다 — 목록만 주면 크리에이터는
                # 다음 회차에 무엇을 준비해야 할지 알 수 없다.
                "reason": cluster["run_note"],
                "error": cluster["run_error"],
                "suggestedSourceId": cluster["suggested_source_id"],
                "texts": _questions(conn, cluster["id"]),
            })
        # 수요 순. 같으면 좋아요 순(demand 가 이미 그렇게 준다).
        return out

    answered = []
    for cluster in found:
        if cluster["status"] not in ("REVIEW", "PUBLISHED"):
            continue
        clip = clusters.clip_of(conn, cluster["id"])
        answered.append({
            "clusterId": cluster["id"],
            "question": cluster["canonical_text"],
            "askedBy": cluster["question_count"],
            "likes": cluster["like_count"],
            "clipId": clip["id"] if clip else None,
            "totalSec": clip["total_sec"] if clip else None,
            "parts": len(clip["parts"]) if clip else 0,
            "published": bool(clip and clip["published_at"]),
        })

    total_questions = conn.execute(
        "select count(*) as n from questions where source_id = %s", (source_id,)
    ).fetchone()["n"]
    unclustered = conn.execute(
        "select count(*) as n from questions where source_id = %s and cluster_id is null",
        (source_id,),
    ).fetchone()["n"]

    return {
        "source": {
            "id": source["id"], "title": source["title"], "channel": source["channel"],
            "durationSec": source["duration_sec"], "published": source["published"],
        },
        "summary": {
            "questions": total_questions,
            "unclustered": unclustered,
            "clusters": len(found),
            "answered": len(answered),
            "missing": len(by_status.get(MISSING, [])),
            "waiting": len(by_status.get(WAITING, [])),
            "declined": len(by_status.get(DECLINED, [])),
        },
        "missing": rows(MISSING),
        "waiting": rows(WAITING),
        "declined": rows(DECLINED),
        "answered": answered,
        "cost": cost_split(conn, cfg, source_id),
    }


def cost_split(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> dict:
    """회차 준비 비용과 질문 답변 비용을 가른다.

    🔴 **가르지 않으면 "질문당 얼마" 가 안 나온다.** 전사·구간 분할·임베딩은 영상당 한 번
    드는 값이고, 답변은 질문마다 든다. 둘을 합쳐 질문 수로 나누면 질문이 하나일 때
    전사비가 통째로 그 질문에 얹힌다.

    가르는 기준은 `run_id` 다 — 답하기가 만든 run 에 묶인 호출만 질문 몫이다.
    🔴 기준(criteria) 경로의 run 은 제외한다. 그건 크리에이터가 자기 기준으로 뽑은 것이지
    시청자 질문에 답한 것이 아니다. `question_clusters.run_id` 로 걸러낸다.
    """
    answer_runs = [
        r["run_id"] for r in conn.execute(
            "select run_id from question_clusters where source_id = %s and run_id is not null",
            (source_id,),
        )
    ]
    columns = ("select stage, model, input_tokens, output_tokens, thinking_tokens,"
               " total_tokens, cached_tokens, run_id from stage_calls where source_id = %s")
    calls = [dict(r) for r in conn.execute(columns, (source_id,))]

    answered = [c for c in calls if c["run_id"] in answer_runs]
    prepare = [c for c in calls if c["run_id"] not in answer_runs]

    answer_cost = pricing.estimate(answered, cfg)
    prepare_cost = pricing.estimate(prepare, cfg)
    # 🔴 분모는 "답한 질문" 이 아니라 **"답하기를 돌린 질문"** 이다. 답하지 못한 질문도 분류와
    # 후보 생성에 돈을 썼다 — 그걸 빼면 단가가 실제보다 싸 보인다.
    attempted = len(answer_runs)
    return {
        "prepare": prepare_cost,
        "answer": answer_cost,
        "attempted": attempted,
        # 🔴 한 건도 안 돌렸으면 null 이다. 0 으로 두면 "질문당 0원" 으로 읽힌다.
        "perQuestionKrw": round(answer_cost["krw"] / attempted, 2) if attempted else None,
        # 🔴 전사는 로컬 연산(faster-whisper)이라 API 비용이 0이다. 이 숫자만 보고 "전사가 싸다"
        # 로 읽으면 안 된다 — 장비 시간은 여기 안 들어 있다.
        "note": "전사는 로컬 연산이라 API 비용에 포함되지 않습니다",
    }
