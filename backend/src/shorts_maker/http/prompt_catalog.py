"""프롬프트 목록 — 무엇이 고정이고 무엇을 누가 채우는가 (읽기 전용 화면용).

프롬프트는 네 층으로 조립된다(`shorts_maker/standards.py` 머리 주석). 1층 규칙은 관리자가 고칠 수 없다 —
출력 형식이 한 글자만 어긋나도 파싱이 실패한다. 대신 **무엇이 고정돼 있는지는 볼 수 있어야** 한다.
관리자 기준이 어디에 들어가고 어디에 안 들어가는지도 여기서 보인다.

🔴 **템플릿 원문을 그대로 보낸다. 요약하지 않는다.** 사람이 따로 적은 설명은 코드가 바뀌면 조용히
어긋난다. 칸(`{context_block}` 같은 것)도 손으로 적지 않고 `string.Formatter` 로 원문에서 뽑는다 —
누가 템플릿에 칸을 새로 넣으면 여기 자동으로 나타나고, 그 칸의 층이 정해져 있지 않으면 테스트가 막는다.

🔴 **관리자 기준이 들어가는지도 원문에서 판단한다**(`standard_block` 칸이 있나). 따로 표시해 두면
"화면엔 적용된다고 나오는데 실제로는 안 들어간다" 가 생긴다.

http 층에 두는 이유: 목록을 모아 보여주는 일이지 도메인 로직이 아니다. 여러 모듈의 템플릿을 한데
읽어야 해서 pipeline·answers 어느 한쪽에 두면 반대쪽을 거꾸로 import 하게 된다.
"""

import string

import psycopg

from .. import standards
from ..answers import judge, routing
from ..pipeline import cutting, ranking, segmentation

# 층. 화면이 이 순서로 설명한다.
LAYERS = [
    {"key": "rule", "label": "1. 규칙", "who": "코드", "editable": False,
     "note": "자립성 관문 · 출력 형식 · 번호 검증. 바꾸면 파싱이 깨질 수 있어 열지 않습니다"},
    {"key": "standard", "label": "2. 관리자 기준", "who": "관리자, 한 번", "editable": True,
     "note": "채널이 공통으로 좋게 보는 것. 선호일 뿐이라 보장이 필요한 규칙은 여기 두지 않습니다"},
    {"key": "context", "label": "3. 영상 개요", "who": "영상마다", "editable": True,
     "note": "그 영상이 무엇인지. 영상 준비 탭에서 적습니다"},
    {"key": "request", "label": "4. 이번 요청", "who": "매번", "editable": False,
     "note": "기준 입력칸의 글, 또는 시청자 질문"},
    {"key": "auto", "label": "자동", "who": "파이프라인", "editable": False,
     "note": "대사 · 구간 목록 · 예산 같은 값. 사람이 채우지 않습니다"},
]

# 🔴 칸 → 층. 템플릿에 새 칸이 생기면 **여기 없으면 테스트가 실패한다** — 그 칸을 누가 채우는지
# 정하지 않은 채 화면에 내보내지 않는다.
LAYER_OF = {
    "standard_block": "standard",
    "context_block": "context",
    "criteria_block": "request",
    "question": "request",
    "text": "request",
    "segment_block": "auto",
    "utterance_block": "auto",
    "segments": "auto",
    "body": "auto",
    "description": "auto",
    "parts_note": "auto",
    "budget": "auto",
    "count": "auto",
    "extra": "auto",
    "limit": "auto",
    "first_idx": "auto",
    "last_idx": "auto",
    "min_sec": "auto",
    "max_sec": "auto",
}

# 단계. 파이프라인이 도는 순서. `why` 만 사람이 적는다 — 나머지는 전부 원문에서 나온다.
STAGES = [
    {"key": "segment", "name": "구간 분할", "path": "영상 준비", "module": "pipeline/segmentation.py",
     "template": segmentation.PROMPT_TEMPLATE,
     "why": "구간은 모든 기준이 다시 쓰는 중립 자산입니다. 취향이 섞이면 기준을 바꿀 때마다 전사부터 다시 해야 합니다"},
    {"key": "rank", "name": "기준 순위", "path": "기준 경로", "module": "pipeline/ranking.py",
     "template": ranking.FIXED_PROMPT,
     "why": "어떤 구간을 좋게 볼지는 선호입니다"},
    {"key": "cut", "name": "자르기", "path": "기준 경로", "module": "pipeline/cutting.py",
     "template": cutting.PROMPT_TEMPLATE,
     "why": "구간 안에서 어디를 자를지는 선호입니다"},
    {"key": "select", "name": "답 구간 찾기", "path": "질문 경로", "module": "answers/routing.py",
     "template": routing.PROMPT,
     "why": "어디에 답이 있는지는 사실 판단입니다. 기준이 섞이면 선호하지 않는 구간이 검색에서 사라집니다"},
    {"key": "answer", "name": "후보 만들기", "path": "질문 경로", "module": "pipeline/ranking.py",
     "template": ranking.ANSWER_PROMPT,
     "why": "여러 방식 중 어떤 컷을 낼지는 선호입니다"},
    {"key": "judge", "name": "판정", "path": "질문 경로", "module": "answers/judge.py",
     "template": judge.PROMPT,
     "why": "점수에만 반영합니다. 자립·답변 두 관문은 고정입니다 — 섞이면 선호가 숨은 탈락 조건이 됩니다"},
]


def slots(template: str) -> list[str]:
    """템플릿의 칸 이름들, 나오는 순서대로(중복 제거)."""
    seen: list[str] = []
    for _, field, _, _ in string.Formatter().parse(template):
        if field and field not in seen:
            seen.append(field)
    return seen


def parts(template: str) -> list[dict]:
    """원문을 글과 칸으로 쪼갠다. 화면이 칸마다 층 색을 입힌다.

    `string.Formatter` 로 쪼개는 이유: `{{` 같은 이스케이프와 `{budget:.0f}` 같은 서식을 정규식으로
    다루면 틀린다. 파이썬이 실제로 채울 때 쓰는 것과 같은 규칙으로 읽는다.
    """
    out: list[dict] = []
    for literal, field, spec, conversion in string.Formatter().parse(template):
        if literal:
            out.append({"kind": "text", "text": literal})
        if field is not None:
            out.append({
                "kind": "slot", "name": field, "spec": spec or "",
                "conversion": conversion or "", "layer": LAYER_OF.get(field, "auto"),
            })
    return out


def stages() -> list[dict]:
    return [
        {
            "key": stage["key"], "name": stage["name"], "path": stage["path"],
            "module": stage["module"], "why": stage["why"],
            # 🔴 원문에서 판단한다. 따로 표시해 두면 화면과 실제가 어긋날 수 있다.
            "usesStandard": "standard_block" in slots(stage["template"]),
            "parts": parts(stage["template"]),
        }
        for stage in STAGES
    ]


def build(conn: psycopg.Connection) -> dict:
    row = standards.latest(conn)
    return {
        "layers": LAYERS,
        "stages": stages(),
        "standard": {
            "body": row["body"] if row else "",
            "updatedAt": row["created_at"] if row else None,
            "maxChars": standards.MAX_CHARS,
        },
    }
