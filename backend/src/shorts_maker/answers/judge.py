"""LLM-as-judge — 잘라낸 대사가 실제로 답이 되는가 (tease §5-7).

파이프라인에 구멍이 하나 있었다. 구간을 고를 때는 **요약**을 보고 판단하는데, 잘라내기는 그
안의 **발화 범위**를 고른다. 잘려나온 실제 대사가 혼자 서는지는 아무도 안 봤다.

judge 는 영상이 아니라 **텍스트**를 본다. 그래서 렌더하기 전에 판정할 수 있고, 후보를 여러 개
만들어 놓고 이긴 것 하나만 렌더할 수 있다. 이게 best-of-3 가 싼 이유다.

⚠️ 이건 "에이전트"가 아니다. 파이프라인은 고정 DAG 라 다음에 뭘 할지 정할 게 없다.
**판정 하나**다. 제출 문서에도 그렇게 쓴다 — 정확한 용어가 심사에서 더 강하다.
"""

import json
from dataclasses import dataclass

from .. import config
from ..adapters import gemini

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "standalone": {"type": "BOOLEAN"},
        "answers": {"type": "BOOLEAN"},
        "score": {"type": "INTEGER"},
        "reason": {"type": "STRING"},
    },
    "required": ["standalone", "answers", "score", "reason"],
}

# 🔴 `answers` 의 눈금은 `ranking.ANSWER_PROMPT` 와 **같아야 한다.** 한쪽만 후하면 계획이 낸
# 후보를 판정이 전부 떨어뜨려 REVIEW 에 NG 만 쌓인다. 완화 근거(실측 1건과 오류 비용의 비대칭)는
# 그쪽 주석에 있다 — 2026-09-09.
PROMPT = """아래는 긴 영상에서 잘라낸 짧은 클립의 **대사 전문**이다. 시청자는 이 클립만 본다 —
앞뒤 맥락을 전혀 모른다.

두 가지를 판정하라.

1. `standalone`: 앞을 보지 않은 사람이 이 대사만으로 이해할 수 있는가.
   - 앞에서 세운 전제 없이 결론만 남아 있거나, "그것은"·"아까 말한" 처럼 앞을 가리키는 말로
     시작하면 false 다.
   - 말이 도중에 시작하거나 문장이 끊긴 채 끝나도 false 다.
2. `answers`: 아래 **질문에 실제로 답하는가.**
   - 기준은 "물어본 사람이 이걸 보고 답을 얻었다고 느끼는가" 다. 질문의 **핵심**에 답하면
     충분하고, 질문에 담긴 낱말을 하나하나 다뤄야 하는 것은 아니다. 부분적이라도 내용이 있으면
     답이다.
   - **주제어만 나오고 그에 대한 내용이 없으면** false 다. 관련은 있지만 다른 얘기를 하면 false 다.

`score` 는 숏폼으로서의 완성도 0~100. 위 둘이 모두 true 여야 높은 점수를 준다.
`reason` 은 한두 문장. 크리에이터가 읽고 판단할 근거다.

질문:
{question}

대사 전문{parts_note}:
{body}"""


class JudgeError(RuntimeError):
    pass


@dataclass
class Verdict:
    standalone: bool
    answers: bool
    score: int
    reason: str

    @property
    def passed(self) -> bool:
        """둘 다 통과해야 합격이다. 점수는 합격한 것들 사이의 순위일 뿐이다."""
        return self.standalone and self.answers


def build_prompt(question: str, part_texts: list[str]) -> str:
    if not part_texts:
        raise JudgeError("판정할 대사가 없다")
    if len(part_texts) == 1:
        body = part_texts[0]
        note = ""
    else:
        # 🔴 조합 클립은 점프가 있다는 걸 judge 에게 알려준다. 모르면 "말이 갑자기 바뀐다"를
        # 자립성 실패로 읽고 조합을 전부 떨어뜨린다.
        body = "\n\n".join(f"[{i + 1}번째 조각]\n{text}" for i, text in enumerate(part_texts))
        note = (
            f" — {len(part_texts)}개 조각을 이어붙인 클립이다. 조각 사이에는 화면에"
            ' "몇 분에서 이어집니다" 안내가 뜨므로, 장면이 바뀌는 것 자체는 문제가 아니다.'
            " 이어붙인 결과가 하나의 답으로 읽히는지를 본다"
        )
    return PROMPT.format(question=question, parts_note=note, body=body)


def parse(raw: str) -> Verdict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"JSON 파싱 실패: {exc}") from exc
    try:
        score = int(payload["score"])
        verdict = Verdict(
            standalone=bool(payload["standalone"]),
            answers=bool(payload["answers"]),
            # 범위를 벗어난 점수는 자르기만 한다 — 판정 자체를 버리기엔 아깝다.
            score=max(0, min(100, score)),
            reason=str(payload["reason"]).strip(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JudgeError(f"응답 형식이 잘못됐다: {payload!r}") from exc
    return verdict


def judge(cfg: config.Config, question: str, part_texts: list[str]) -> tuple[Verdict, dict, int]:
    """(판정, 사용량, 소요 ms). 🔴 **DB 를 만지지 않는다** — 여러 후보를 스레드로 동시에
    판정하기 때문이다(answers/answer.py). 기록은 부른 쪽이 한 곳에서 한다."""
    raw, usage, latency_ms = gemini.generate_json(
        cfg, build_prompt(question, part_texts), SCHEMA
    )
    return parse(raw), usage, latency_ms
