"""관리자 기준 — 채널이 공통으로 좋게 보는 것 (프롬프트 2층).

프롬프트는 네 층으로 조립된다.

| 층 | 누가 정하나 | 무엇 | 어디 |
|---|---|---|---|
| 1. 규칙 | 코드 | 자립성 관문 · 출력 형식 · 번호 검증 | 각 모듈의 템플릿. **읽기만 된다** |
| 2. 관리자 기준 | 관리자, 한 번 | "숫자·조건·기한이 있는 발화 우선", 톤, 피할 주제 | **이 모듈** |
| 3. 영상 개요 | 영상마다 | "쏘카 개발 직군 채용설명회" | `sources.context` |
| 4. 이번 요청 | 매번 | 기준 입력 또는 시청자 질문 | 호출 인자 |

1층을 관리자에게 열지 않는 이유: 출력 형식이 한 글자만 어긋나도 파싱이 실패하고, 자립성 관문이
흔들리면 앞뒤를 모르면 이해 못 하는 클립이 조용히 발행된다. 대신 **무엇이 고정돼 있는지 화면에서
볼 수 있게** 한다(`http/prompt_catalog.py`).

## 🔴 어디에 넣고 어디에 안 넣나

**넣는다** — 선호가 결과를 바꿔야 하는 자리:
  - 기준 경로의 순위 매기기(`ranking.build_prompt`)와 자르기(`cutting.build_prompt`)
  - 질문 경로의 후보 생성(`ranking.build_answer_prompt`)
  - 판정의 **점수**(`judge.build_prompt`). 🔴 자립·답변 두 관문에는 반영하지 않는다.
    관문에 섞이면 "경쟁사 언급은 피한다" 같은 선호가 **숨은 탈락 조건**이 되어 답이 조용히 사라지고,
    사람·모델 판정 일치율이라는 품질 지표도 뜻을 잃는다. 판정에 아예 안 넣으면 후보는 기준대로
    뽑는데 추천은 기준을 무시해서 어긋난다 — 그래서 점수에만 넣는다.

**안 넣는다** — 사실을 다루는 자리:
  - 구간 분할(`segmentation`). 🔴 구간은 캐시해서 **모든 기준이 재사용하는 중립 자산**이다.
    취향이 섞이면 기준을 바꿀 때마다 전사부터 다시 해야 한다(CLAUDE.md "중립/주관을 섞지 않는다").
  - 답 구간 찾기(`routing`). "어디에 답이 있나" 는 선호가 아니라 사실 판단이다. 기준이 섞이면
    선호하지 않는 구간이 **검색에서 아예 사라져** 답할 수 있는 질문을 못 답하게 된다.

## 🔴 약한 신호다

지난 실측에서 프롬프트의 "지시대명사로 시작하지 않는다" 를 모델이 지키지 않았다(cutting.lead_in 주석).
기준도 같다. **보장이 필요한 규칙**("경쟁사 이름은 절대 내보내지 않는다")은 여기가 아니라 코드 필터로
간다. 이 글은 선호를 적는 자리다.

## 한 run 에 한 번 읽는다

답하기는 후보 생성과 판정이 몇 분 떨어져 돈다. 그 사이에 관리자가 기준을 고치면 후보는 옛 기준으로,
점수는 새 기준으로 매겨져 추천이 어긋난다. 그래서 호출부가 run 시작에 한 번 읽어 양쪽에 넘긴다.
"""

import psycopg

# 🔴 마이그레이션 007 의 CHECK 와 같은 값이어야 한다. 여기서 먼저 막아 사람이 읽을 수 있는 이유로 돌려준다.
MAX_CHARS = 1000

# 순위·후보·자르기용 머리. 선호라는 것과, 이번 요청과 부딪히면 이번 요청이 이긴다는 것을 못박는다 —
# 채널 공통 기준보다 이번에 콕 집어 요청한 것이 더 구체적이다.
HEADER = (
    "관리자 기준 — 이 채널이 공통으로 좋게 보는 것이다. 선호일 뿐이다. "
    "위의 규칙과 출력 형식은 그대로 지키고, 이번 요청과 부딪히면 이번 요청을 따른다:\n"
)

# 판정용 머리. 🔴 점수에만 쓰고 두 관문에는 쓰지 않는다고 적는다 — 이유는 머리 주석.
SCORE_HEADER = (
    "관리자 기준 — 이 채널이 공통으로 좋게 보는 것이다. **score 에만 반영한다.** "
    "standalone 과 answers 판정에는 반영하지 않는다:\n"
)


class StandardError(ValueError):
    pass


def latest(conn: psycopg.Connection) -> dict | None:
    """최신 행(화면용). 한 번도 저장한 적 없으면 None."""
    row = conn.execute(
        "select id, body, created_at from admin_standards order by id desc limit 1"
    ).fetchone()
    return dict(row) if row else None


def load(conn: psycopg.Connection) -> str | None:
    """프롬프트에 넣을 기준. 없거나 비었으면 None."""
    row = latest(conn)
    body = (row["body"] if row else "") or ""
    return body.strip() or None


def save(conn: psycopg.Connection, body: str) -> dict:
    """새 기준을 **덧붙인다**(append-only). 빈 글은 "기준 없음" 으로 저장된다. 커밋은 호출부가 한다."""
    text = (body or "").strip()
    if len(text) > MAX_CHARS:
        raise StandardError(f"기준은 {MAX_CHARS}자까지다 (지금 {len(text)}자)")
    row = conn.execute(
        "insert into admin_standards (body) values (%s) returning id, body, created_at", (text,)
    ).fetchone()
    return dict(row)


def block(standard: str | None) -> str:
    """순위·후보·자르기 프롬프트에 끼울 덩어리.

    🔴 **비면 줄 자체를 뺀다.** 빈 지시문을 남기면 모델이 그걸 해석한다(ranking 의 기준 입력과 같은 규칙).
    """
    if not standard or not standard.strip():
        return ""
    return f"\n{HEADER}{standard.strip()}\n"


def score_block(standard: str | None) -> str:
    """판정 프롬프트에 끼울 덩어리. 점수에만 반영한다고 명시한다."""
    if not standard or not standard.strip():
        return ""
    return f"\n{SCORE_HEADER}{standard.strip()}\n"
