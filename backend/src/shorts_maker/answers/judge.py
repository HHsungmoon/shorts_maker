"""LLM-as-judge — 잘라낸 대사가 실제로 답이 되는가 (tease §5-7).

파이프라인에 구멍이 하나 있었다. 구간을 고를 때는 **요약**을 보고 판단하는데, 잘라내기는 그
안의 **발화 범위**를 고른다. 잘려나온 실제 대사가 혼자 서는지는 아무도 안 봤다.

judge 는 영상이 아니라 **텍스트**를 본다. 그래서 렌더하기 전에 판정할 수 있고, 후보를 여러 개
만들어 놓고 이긴 것 하나만 렌더할 수 있다. 이게 best-of-3 가 싼 이유다.

⚠️ 이건 "에이전트"가 아니다. 파이프라인은 고정 DAG 라 다음에 뭘 할지 정할 게 없다.
**판정 하나**다. 제출 문서에도 그렇게 쓴다 — 정확한 용어가 심사에서 더 강하다.

## 판정기가 둘이다 (2026-10-31)

  `judge()`     — Gemini. 두 관문(T/F) + 점수 + 이유를 JSON 하나로. 1회 입력 443 · **사고 828** · 출력 77,
                  p50 5.9초(로컬 실측 9건). 비용의 90% 가 사고 토큰이다
  `judge_jev()` — Jev. 같은 세 판단을 **서로 다른 질문 셋**으로 나눠 한 요청에 보낸다. 사고 과정이 없고
                  p50 수백 ms 다. 대신 이유 문장을 쓰지 못한다 — 확률을 그대로 보여준다

🔴 두 판정기는 **같은 눈금**을 쓴다(아래 PROMPT 와 JEV_QUESTIONS 의 문구). 한쪽만 후하면 엔진을 바꾼
순간 합격률이 바뀌어, 그 차이가 판정기 탓인지 후보 탓인지 가를 수 없다.
"""

import json
from dataclasses import dataclass

from .. import config, standards
from ..adapters import gemini, jev

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
{standard_block}
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
    # Jev 판정에서만 채워진다. 🔴 T/F 는 이 확률을 합격선(PASS)에 댄 결과일 뿐이다 — 합격선을 다시
    # 잡으려면 원래 확률이 있어야 해서 stage_calls.params 에 함께 남긴다.
    p_standalone: float | None = None
    p_answers: float | None = None

    @property
    def passed(self) -> bool:
        """둘 다 통과해야 합격이다. 점수는 합격한 것들 사이의 순위일 뿐이다."""
        return self.standalone and self.answers


def build_prompt(question: str, part_texts: list[str], standard: str | None = None) -> str:
    """`standard` 는 관리자 기준. 🔴 **score 에만** 반영한다 — 두 관문에 섞으면 선호가 숨은 탈락
    조건이 된다(standards.py 머리 주석)."""
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
    return PROMPT.format(
        question=question, parts_note=note, body=body,
        standard_block=standards.score_block(standard),
    )


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


@dataclass
class Scored:
    """판정을 받은 후보 하나 — 후보(라벨·이유·줄 범위), 실제 조각, 판정. 두 엔진이 같은 모양으로 낸다."""

    candidate: object
    parts: list
    verdict: Verdict


@dataclass
class Judged:
    """판정 한 건과 그 호출의 흔적.

    프롬프트와 원본 응답을 함께 들고 나오는 이유: "왜 NG 인가" 를 확인하려면 judge 가 **어떤
    대사를 보았는지**를 알아야 한다. 자른 텍스트는 clip_parts 로 되짚을 수 있지만 그건 승자
    하나뿐이고, 떨어진 후보 둘은 어디에도 남지 않는다(마이그레이션 005).
    """

    verdict: Verdict
    usage: dict
    latency_ms: int
    prompt: str
    raw: str


def judge(
    cfg: config.Config, question: str, part_texts: list[str], standard: str | None = None
) -> Judged:
    """🔴 **DB 를 만지지 않는다** — 여러 후보를 스레드로 동시에 판정하기 때문이다
    (answers/answer.py). 기록은 부른 쪽이 한 곳에서 한다."""
    prompt = build_prompt(question, part_texts, standard)
    raw, usage, latency_ms = gemini.generate_json(cfg, prompt, SCHEMA)
    return Judged(parse(raw), usage, latency_ms, prompt, raw)


# ---------------------------------------------------------------- Jev

# 🔴 합격선. Noul 확률이 이 값 이상이면 그 관문을 통과한 것으로 본다.
#
# 0.5 는 출발점이다 — 아직 우리 데이터로 재지 않았다. Jev 는 한국어 정확도가 영어보다 낮다고 공식
# 문서가 적고(adapters/jev.py), 『Jev 따라하기』 4·11부는 확률을 "정답일 확률" 로 읽지 말고 자기
# 데이터로 보정하라고 한다. 사람이 고른 후보(run_candidates.chosen_at)와 reviewer 판정(clip_reviews)이
# 쌓이면 다시 정한다. 그때 필요한 원래 확률은 stage_calls.params 에 다 있다.
# TODO: 정답 라벨 20~30건으로 합격선 다시 잡기 (upgrade_plan)
PASS = 0.5

# 완성도 눈금. 🔴 **영어로 쓴다** — Jev 의 주 학습 언어가 영어다. 판단할 대사(state)만 한국어다.
# 다섯 칸인 이유: 기대값이 0~4 라 0~100 으로 펴도 칸 사이가 25점이라 순위가 잘 갈린다. 칸이 많으면
# 이웃한 칸의 뜻이 겹쳐 확률이 흩어진다.
QUALITY_LEVELS = [
    "Useless: says nothing about the question",
    "Weak: touches the topic but the viewer learns little",
    "Acceptable: a partial answer the viewer can use",
    "Good: a clear answer with some substance",
    "Excellent: a complete, concrete answer that works on its own as a short video",
]

# 🔴 문구는 위 PROMPT 의 두 관문과 같은 눈금이다(머리 주석). "주제어만 나오면 아니다", "핵심에 답하면
# 충분하다" 가 그대로 옮겨져 있다.
JEV_QUESTIONS = {
    "standalone": {
        "type": "noul",
        "instructions": (
            "`clip` is the full transcript of a short clip cut from a long Korean video. The viewer sees"
            " only this clip and knows nothing that came before it. Can such a viewer understand `clip`"
            " on its own? `before_clip`, when present, is what was said right before the clip; the viewer"
            " does NOT see it. If understanding `clip` correctly needs something from `before_clip` (for"
            " example which topic its numbers or rules belong to), answer false."
        ),
        "criteria": {
            "true": "It makes sense without the earlier talk",
            "false": (
                "It relies on earlier talk: it opens by pointing back (for example 이런, 그렇게, 그래서,"
                " 아까 말한), only states a conclusion whose premise was set up earlier, or starts or ends"
                " mid-sentence"
            ),
        },
    },
    "answers": {
        "type": "noul",
        "instructions": (
            "Does `clip` actually answer `question`? Judge it the way the person who asked would: would"
            " they feel they got an answer? Use `before_clip`, when present, only to know what `clip` is"
            " really about; it is not shown to the viewer."
        ),
        "criteria": {
            "true": (
                "It addresses the core of the question, even partially. It does not have to cover every"
                " word in the question"
            ),
            "false": (
                "The topic word appears but nothing is said about it, or it talks about something related"
                " but different"
            ),
        },
    },
    "quality": {
        "type": "score",
        "instructions": "How good is `clip` as a short-form video that answers `question`?",
        "criteria": QUALITY_LEVELS,
    },
}


# 클립 앞의 대사를 몇 발화까지 함께 보여줄까. 🔴 이유는 jev_state 주석.
BEFORE_LINES = 2


def jev_state(question: str, part_texts: list[str], before: str | None = None) -> dict:
    """Jev 가 판단할 내용. 시청자가 보는 것은 `clip` 뿐이다.

    🔴 `before`(클립 바로 앞 대사)를 **"시청자는 못 본다" 고 못박아** 함께 준다. 클립만 주면 판정기가
    빠진 것이 있는지 알 수 없다. 실측(2026-10-31, 서강대 "교과전형과 지역균형전형의 최저 기준"):
    "국어, 수학, 영어, 탐구… 7이내" 만 잘린 범위가 자립 0.54 · 답함 0.60 으로 1등이 됐다. 바로 앞
    발화가 "이 논술전형 선발할 때" 였다 — **논술 기준**이다. 앞을 모르는 판정기에게 그 숫자는 그냥
    "최저 기준" 이었다. Gemini 판정기는 이 정보 없이 판정해 왔고, 그 경로는 rank 가 전체 대사를 보고
    범위를 골라서 이 구멍이 덜 드러났다.
    """
    if not part_texts:
        raise JudgeError("판정할 대사가 없다")
    state: dict = {"question": question}
    if before and before.strip():
        state["before_clip"] = before.strip()
    if len(part_texts) == 1:
        state["clip"] = part_texts[0]
    else:
        # 🔴 조합 클립은 점프가 있다는 걸 알려준다 — Gemini 판정기와 같은 이유(build_prompt 주석).
        state["clip"] = [f"[part {i + 1}] {text}" for i, text in enumerate(part_texts)]
        state["note"] = (
            f"The clip joins {len(part_texts)} parts from different moments of the video. Between parts"
            " the screen shows a card saying where the next part comes from, so the jump itself is not a"
            " flaw. Judge whether the joined parts read as one answer."
        )
    return state


def jev_questions(standard: str | None = None) -> dict:
    """세 질문. 관리자 기준은 🔴 **quality 에만** 붙인다 — 두 관문에 섞으면 선호가 숨은 탈락 조건이 된다
    (standards.py 머리 주석). Gemini 판정기는 프롬프트 한 덩어리 안에서 문장으로 이 경계를 지켰는데,
    여기서는 질문이 나뉘어 있어 **구조가 경계다** — 두 관문 질문은 기준 문구를 아예 보지 못한다."""
    questions = json.loads(json.dumps(JEV_QUESTIONS))
    if standard and standard.strip():
        questions["quality"]["instructions"] += (
            " The channel also prefers the following (a preference written in Korean by the channel"
            f" owner; let it shape this score only): {standard.strip()}"
        )
    return questions


def verdict_of(answers: dict) -> Verdict:
    p_standalone = answers["standalone"]["noul"]
    p_answers = answers["answers"]["noul"]
    quality = answers["quality"]
    score = jev.score_to_100(quality["score"], quality["levels"])
    return Verdict(
        standalone=p_standalone >= PASS,
        answers=p_answers >= PASS,
        score=score,
        # 🔴 Jev 는 이유를 쓰지 못한다. 크리에이터가 읽는 자리(clip_reviews.note · 후보 카드)에는 확률을
        # 그대로 둔다 — 지어낸 문장보다 "자립 0.45" 가 무엇을 다시 볼지 더 정확히 말한다.
        reason=(
            f"자립 {p_standalone:.2f} · 답함 {p_answers:.2f} · 완성도 {quality['score']:.1f}/"
            f"{quality['levels'] - 1} (Jev, 합격선 {PASS:.2f})"
        ),
        p_standalone=p_standalone,
        p_answers=p_answers,
    )


def judge_jev(
    cfg: config.Config, question: str, part_texts: list[str], standard: str | None = None,
    before: str | None = None,
) -> Judged:
    """Jev 로 한 번 판정한다. 🔴 `judge()` 와 같이 **DB 를 만지지 않는다** — 수십 개를 스레드로 돌린다."""
    state = jev_state(question, part_texts, before)
    questions = jev_questions(standard)
    answers, usage, latency_ms, raw = jev.system_one(cfg, state, questions)
    request = json.dumps({"state": state, "questions": questions}, ensure_ascii=False)
    return Judged(verdict_of(answers), usage, latency_ms, request, raw)
