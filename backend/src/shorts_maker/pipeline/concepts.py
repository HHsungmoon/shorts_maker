"""관리자 숏폼의 컨셉 셋 — 브랜딩 · 바이럴 · 정보성 (tease §5-11, 2026-11-08).

시청자 질문에 답하는 숏폼(질문 경로)과 따로, 관리자가 새 클립 탭에서 직접 만드는 숏폼은 **무엇을 노리는가**가 셋으로
갈린다. 같은 구간이라도 컨셉에 따라 좋은 숏폼이 다르다 — 채용설명회의 "우리 팀은 이렇게 일한다" 는 브랜딩에 맞고,
"서류에서 바로 떨어지는 세 가지" 는 정보성 · 바이럴 둘 다에 맞는다.

컨셉은 기준 경로의 run 에 붙는다(`runs.concept`). 그 run 의 세 단계가 같은 컨셉을 읽는다:
    고르기(ranking)  — 무엇을 좋게 볼까          → goal
    자르기(cutting)  — 구간 안에서 어디를 자를까 → goal + cut
    AI 카피(headlines) — 어떤 말투로 쓸까         → goal + copy
추천(ideas)은 셋의 goal 을 다 보여 주고 이 영상에 맞는 컨셉과 주제를 고르게 한다.

층으로는 4층(이번 요청)이다 — 관리자가 매번 고른다. 글은 여기 있고 프롬프트 화면에서 보이기만 한다(`http/prompt_catalog`).
TODO: 채널마다 컨셉 글을 고치고 싶어지면 관리자 기준(standards)처럼 화면에서 고치는 글로 연다.

🔴 키(brand · viral · info)는 마이그레이션 023 의 CHECK 와 같아야 한다 — tests/pipeline/test_concepts.py 가 지킨다.
🔴 자립성 관문은 컨셉과 무관하게 그대로다(ranking 1단계). 바이럴이라도 앞을 모르면 이해 못 하는 구간은 뺀다.
🔴 글은 "무엇을 좋게 본다" 로 쓴다. 모델에게 금지를 늘어놓는 대신 점수의 눈금을 준다(CLAUDE.md 규약).
"""

# 화면 · 프롬프트가 이 순서로 보인다. 추천(ideas)도 이 순서로 컨셉을 늘어놓는다.
CONCEPTS: list[dict] = [
    {
        "key": "brand",
        "label": "브랜딩",
        "summary": "회사 · 조직을 기억하게 합니다 — 일하는 방식 · 문화 · 사람",
        "goal": (
            "브랜딩 홍보 숏폼이다. 노리는 것은 조회수보다 **이 회사 · 조직을 기억하게 하는 것**이다. "
            "일하는 방식 · 문화 · 사람 · 가치 · 비전이 드러나고, 보고 나면 '이 회사 괜찮다' 는 인상이 남는 대목을 좋게 본다. "
            "말하는 사람이 자기 회사나 팀을 자기 말로 설명하는 대목이 특히 좋다. "
            "회사를 깎아내리거나 내부 사정을 흘리는 대목은 낮게 본다."
        ),
        "cut": "회사 · 팀의 이름이나 하는 일이 드러나는 문장이 클립 안에 들어오게 자른다.",
        "copy": "회사 · 팀과 일하는 방식이 떠오르게 쓴다. 부풀리지 않고 믿음이 가는 말투로 쓴다.",
        "examples": [
            "회사가 일하는 방식을 자기 말로 설명하는 대목",
            "팀의 문화 · 분위기가 드러나는 이야기",
            "이 회사에서 일하는 보람을 말하는 대목",
        ],
    },
    {
        "key": "viral",
        "label": "바이럴",
        "summary": "조회수 · 공유를 노립니다 — 첫 3초에 멈추게 하는 한마디",
        "goal": (
            "바이럴 숏폼이다. 노리는 것은 **조회수와 공유**다. 첫 3초에 스크롤을 멈추게 하는 대목을 좋게 본다 — "
            "통념을 뒤집는 말 · 단정적인 한마디 · 의외의 사실 · 웃음이나 공감이 터지는 순간 · 감정이 실린 이야기. "
            "잘라 내도 말한 사람의 뜻이 그대로인 대목만 높게 본다 — 앞뒤를 빼서 뜻이 달라지는 대목은 낮게 본다."
        ),
        "cut": "가장 센 한마디가 클립 앞쪽(첫 몇 초)에 오도록 시작점을 고른다. 끝은 여운이 남는 문장에서 끊는다.",
        "copy": "스크롤을 멈추게 세게 쓴다 — 반전 · 단정 · 질문. 대사에 없는 말로 부풀리지는 않는다.",
        "examples": [
            "통념을 뒤집는 한마디",
            "청중이 웃거나 놀라는 순간",
            "단정적으로 잘라 말하는 대목",
        ],
    },
    {
        "key": "info",
        "label": "정보성",
        "summary": "바로 쓸 수 있는 구체적인 정보 — 숫자 · 기준 · 방법",
        "goal": (
            "정보성 숏폼이다. 노리는 것은 **보는 사람이 바로 쓸 수 있는 구체적인 정보**다. "
            "숫자 · 기준 · 조건 · 절차 · 기한 · 방법이 분명하게 나오는 대목을 좋게 본다. "
            "정보가 한 클립 안에서 끝까지 전달되는 대목이 좋다 — 목록의 일부만 나오고 끊기는 대목이나 모호한 일반론은 낮게 본다."
        ),
        "cut": "정보의 처음과 끝(조건 · 숫자 · 결론)이 모두 클립 안에 들어오게 자른다. 목록이면 항목이 다 들어가게 한다.",
        "copy": "무엇을 알려 주는지 분명하게 쓴다 — 대상 · 숫자 · 결과.",
        "examples": [
            "숫자나 기준이 나오는 대목",
            "절차 · 방법을 차례로 설명하는 대목",
            "자주 묻는 조건에 분명히 답하는 대목",
        ],
    },
]

BY_KEY: dict[str, dict] = {concept["key"]: concept for concept in CONCEPTS}
KEYS: tuple[str, ...] = tuple(BY_KEY)

# 단계마다 goal 뒤에 붙는 한 줄. 고르기는 점수의 눈금만 바꾼다 — 자립성 관문(1단계)은 그대로다.
_RANK_HINT = "2단계 점수는 이 컨셉으로 얼마나 좋은 숏폼이 되는지로 매긴다. 1단계(자립성)는 컨셉과 무관하게 그대로다."
STAGES = ("rank", "cut", "copy")


class ConceptError(ValueError):
    pass


def check(key: str | None) -> str | None:
    """화면 · CLI 에서 온 값. 비면 None(컨셉 없음), 모르는 값이면 ConceptError — 조용히 빼면 그 run 이 컨셉 없이 돈다."""
    if key is None or not key.strip():
        return None
    key = key.strip()
    if key not in BY_KEY:
        raise ConceptError(f"모르는 컨셉: {key} (가능: {', '.join(KEYS)})")
    return key


def block(key: str | None, stage: str) -> str:
    """고르기 · 자르기 · 카피 프롬프트에 끼울 덩어리.

    🔴 **컨셉이 없으면 줄 자체를 뺀다**(컨셉 이전의 run · 질문 경로). 빈 지시문을 남기면 모델이 그걸 해석한다
    (ranking 의 기준 입력 · standards.block 과 같은 규칙).
    """
    if stage not in STAGES:
        raise ConceptError(f"모르는 단계: {stage}")
    if key is None:
        return ""
    concept = BY_KEY.get(key)
    if concept is None:
        raise ConceptError(f"모르는 컨셉: {key}")
    hint = {"rank": _RANK_HINT, "cut": concept["cut"], "copy": concept["copy"]}[stage]
    return f"\n이번 숏폼의 컨셉 — {concept['label']}\n{concept['goal']}\n{hint}\n"


def catalog() -> list[dict]:
    """화면용. 새 클립 탭(고르기 · 예시)과 프롬프트 화면(글 원문)이 같은 목록을 본다."""
    return [dict(concept, examples=list(concept["examples"])) for concept in CONCEPTS]
