"""[답하기] 후보 만들기 — jev 엔진 (2026-10-31, `SHORTS_ANSWER_ENGINE=jev`).

gemini 엔진에서는 rank(Gemini)가 대사 수백 줄을 읽고 후보 N 개의 시작·끝 줄을 **글로** 써 줬다.
가장 무거운 단계였다 — 입력 1290 · 사고 2375 · 출력 355 토큰, p50 13초(로컬 실측 3건), 답하기 비용의 절반.
게다가 프롬프트의 규칙("앞을 가리키는 말로 시작하지 마라")을 지키지 않아 코드가 시작점을 다시 당겼다
(cutting.LEAD_IN_MARKERS 의 실측).

여기서는 그 일을 셋으로 나눈다. **AI 는 판단하고 코드는 통제한다.**

    ① 위치 찾기  Jev 1회   구간마다 "여기에 답이 있나"(Noul) + "어느 줄이 답의 핵심인가"(Choice, 줄 번호가 선택지)
    ② 범위 뽑기  코드      핵심 줄 둘레에서 **자를 수 있는 범위를 전부** 만든다 — 끊기지 않은 발화 안, 예산 이내,
                           앞을 가리키지 않는 문장 첫머리에서 시작, 문장이 끝나는 곳에서 끝
    ③ 판정      Jev ×N    범위마다 judge.judge_jev — 자립(Noul) · 답함(Noul) · 완성도(Score), 병렬
    ④ 고르기    코드      합격 → 점수 순, **서로 많이 겹치는 것은 하나만**, 상위 N 개(SHORTS_ANSWER_CANDIDATES)

②의 규칙은 원래 rank 프롬프트에 문장으로 적혀 있던 것이다. 규약대로 "하지 마라" 고 부탁하는 대신
**입력에서 뺐다** — 규칙을 어기는 범위는 애초에 판정 대상이 되지 않는다. 시각은 처음부터 끝까지
전사 타임스탬프에서 나온다.

비용(2026-10-31 실측 기준 추정): 위치 찾기 3천~2만 토큰 1회 + 판정 500토큰 × 수십 회. 입력 $0.042/1M 이라
답하기 한 번에 1원 안팎이다. Gemini 는 경로 판정(classify) 1회만 남는다.

🔴 rank 경로(찾을 대상이 없는 요청 — "핵심만 30초")는 여기로 오지 않는다(answer.py). 그 요청에는
"답의 핵심 줄" 이 없고, 전 구간(수십 개, 대사 천여 줄)을 한 번에 보내면 Jev 의 질문당 문맥(32k 토큰)을 넘는다.

🔴 Jev 는 한국어 정확도가 영어보다 낮다(adapters/jev.py 머리 주석). 그래서 판정마다 원래 확률을
stage_calls.params 에 남긴다 — 합격선(judge.PASS)과 아래 숫자들은 그걸로 다시 잡는다.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import jev
from ..pipeline import cutting, ranking
from . import judge

# 핵심 줄을 몇 개까지 잡을까. 🔴 서로 다른 자리에서 답하는 후보가 있어야 고를 것이 생긴다 — 한 자리
# 둘레의 변주만으로 다섯을 채우면 크리에이터에게는 같은 카드 다섯 장이다.
ANCHORS = 4
# 같은 구간 안에서 핵심 줄끼리 이만큼은 떨어져 있어야 따로 센다. 붙어 있으면 같은 답의 다른 문장이다.
ANCHOR_GAP_LINES = 3
# 🔴 1등 핵심 줄 무게의 이 비율보다 가벼운 줄은 핵심 줄로 잡지 않는다.
# 실측(2026-10-31, 서강대 질문 3개): 개수(ANCHORS)만 채우게 두자 답이 없는 구간에서도 핵심 줄이 잡혀
# "저희가 인문과 자연 두 가지 유형의 논술이 있고요."(답함 0.02) 같은 후보가 다섯 자리 중 하나를 차지했다.
# 구간 Noul 이 0.1~0.2 인 곳의 줄은 무게가 1등의 1/10 안팎이었다.
ANCHOR_FLOOR = 0.25

# 핵심 줄에서 시작점을 몇 줄까지 거슬러 찾을까 / 끝점을 몇 줄까지 내려가 찾을까.
# 실측(서강대 45줄): 발화 하나가 평균 5.8초라 60초 예산이면 10줄 안팎이다(앞 6 + 뒤 8 이면 넉넉하다). 앞은 맥락(전제를 세우는 문장),
# 뒤는 답의 나머지가 들어갈 자리라 뒤를 조금 더 본다.
MAX_BACK_LINES = 6
MAX_FWD_LINES = 8
# 핵심 줄 하나에서 시작점·끝점 후보를 각각 몇 개까지. 3 × 4 = 12 범위가 상한이다.
STARTS_PER_ANCHOR = 3
ENDS_PER_ANCHOR = 4
# 이보다 짧으면 숏폼이 아니라 인용이다.
MIN_SEC = 8.0
# 판정할 범위 수 상한. 🔴 Jev 한도는 조직별 초당 요청·토큰이다(docs.typesafe.ai/models#rate-limits) —
# 넘으면 429 이고 어댑터가 기다렸다 다시 보낸다. 이 상한이 한 번의 답하기가 몰아 보내는 양을 묶는다.
MAX_SPANS = 48
JUDGE_WORKERS = 8

# 조합(흩어진 답 잇기) 재료는 앵커마다 답하는 단일 범위 위에서 몇 개까지, 지은 짝은 몇 개까지 판정할까.
# 앵커 4 × 3 = 12개 재료 — 같은 앵커끼리는 겹쳐서 짝이 안 되고 예산에도 걸러져 짝은 훨씬 적다.
# 판정 한 번이 0.23초라 병렬 8이면 12개도 1초 안팎이다.
COMBO_PER_ANCHOR = 3
MAX_COMBOS = 12

# 두 후보가 이만큼 겹치면 같은 후보로 본다(짧은 쪽 줄 수 대비 겹친 줄 수).
OVERLAP = 0.5

# 🔴 "이 영상엔 답이 없다" 로 볼 위치 찾기 확률. **낮게 잡는다** — 틀리는 방향을 고른다면 후하게 틀리는
# 쪽이다(ranking.ANSWER_PROMPT 머리 주석의 비대칭: 거짓 부정은 크리에이터가 확인할 방법이 없고, 거짓
# 긍정은 REVIEW 에서 사람이 거른다). 서강대 실측에서 답이 있는 질문은 0.93 이었다.
EXISTS_MIN = 0.2


@dataclass
class Located:
    """위치 찾기 결과. `line_weight[n]` = 그 구간에 답이 있을 확률 × 구간 안에서 그 줄이 핵심일 확률."""

    exists: float
    segment_prob: dict[int, float]
    line_weight: dict[int, float]


@dataclass
class Span:
    """판정할 후보 하나. `ranges` 는 (시작 줄, 끝 줄) 이고 시간 순이다."""

    ranges: list[tuple[int, int]]
    anchor: int
    parts: list[cutting.PartSpec]

    @property
    def total_sec(self) -> float:
        return sum(p.length for p in self.parts)

    def line_set(self) -> set[int]:
        return {n for start, end in self.ranges for n in range(start, end + 1)}


@dataclass
class Proposal:
    """answer.py 로 돌려줄 것. `scored` 는 이미 **고른** 후보다(상위 N 개, 겹침 제거 후)."""

    answerable: bool
    reason: str
    scored: list
    judge_ms: int
    summary: dict


# ---------------------------------------------------------------- ① 위치 찾기

def _line_id(n: int) -> str:
    return f"L{n:03d}"


# 🔴 Choice 의 선택지 상한(docs.typesafe.ai/api). 구간 하나가 이보다 길면 창으로 나눈다 — 창마다 확률의
# 합이 1 이라 창끼리는 비교가 거칠어지지만, 30분 청크의 구간이 255줄을 넘은 적은 아직 없다.
MAX_CHOICES = 255


def locate_request(question: str, lines: list[dict]) -> tuple[dict, dict, dict[str, list[int]]]:
    """(state, questions, 질문 이름 → 그 질문이 다루는 줄 번호). 순수 함수 — 테스트가 모양을 본다."""
    if not lines:
        raise ValueError("대사가 없다")
    transcript: list[str] = []
    by_segment: dict[int, list[dict]] = {}
    current = None
    for line in lines:
        if line["segment_id"] != current:
            current = line["segment_id"]
            # 구간 설명은 넣지 않는다 — 판단할 것은 요약이 아니라 실제 대사다. 머리글은 경계 표시뿐이다.
            transcript.append(f"[segment {line['segment_idx']}]")
        transcript.append(f"{_line_id(line['line'])}| {line['text']}")
        by_segment.setdefault(line["segment_idx"], []).append(line)
    state = {"question": question, "transcript": "\n".join(transcript)}

    questions: dict = {
        "exists": {
            "type": "noul",
            "instructions": "Does any part of `transcript` (a Korean talk) answer `question`, even partially?",
        },
    }
    covers: dict[str, list[int]] = {}
    for idx, group in by_segment.items():
        ids = [line["line"] for line in group]
        span = f"lines {_line_id(ids[0])}-{_line_id(ids[-1])}"
        questions[f"has_{idx}"] = {
            "type": "noul",
            "instructions": f"Does segment {idx} of `transcript` ({span}) answer `question`, even partially?",
        }
        for window, start in enumerate(range(0, len(ids), MAX_CHOICES)):
            chunk = ids[start:start + MAX_CHOICES]
            name = f"where_{idx}" if len(ids) <= MAX_CHOICES else f"where_{idx}_{window}"
            questions[name] = {
                "type": "choice",
                "instructions": (
                    f"Within segment {idx} of `transcript`, which single line is the core of the answer"
                    " to `question`?"
                ),
                # 선택지 설명은 비운다 — 줄 내용은 이미 state 안에 그 번호로 있다(cookbooks/semantic_find).
                "criteria": {_line_id(n): None for n in chunk},
            }
            covers[name] = chunk
        covers[f"has_{idx}"] = ids
    return state, questions, covers


def read_located(answers: dict, covers: dict[str, list[int]], lines: list[dict]) -> Located:
    segment_of = {line["line"]: line["segment_idx"] for line in lines}
    segment_prob = {
        int(name.removeprefix("has_")): answers[name]["noul"]
        for name in covers if name.startswith("has_")
    }
    weight: dict[int, float] = {}
    for name, ids in covers.items():
        if not name.startswith("where_"):
            continue
        for key, p in answers[name]["probabilities"].items():
            n = int(key.removeprefix("L"))
            if n in segment_of:
                weight[n] = segment_prob.get(segment_of[n], 0.0) * p
    return Located(answers["exists"]["noul"], segment_prob, weight)


def anchors_of(located: Located, lines: list[dict], limit: int = ANCHORS) -> list[int]:
    """무게가 큰 줄부터, 같은 구간에서 너무 붙은 것은 건너뛰며 `limit` 개."""
    segment_of = {line["line"]: line["segment_id"] for line in lines}
    picked: list[int] = []
    ordered = sorted(located.line_weight.items(), key=lambda kv: (-kv[1], kv[0]))
    floor = ordered[0][1] * ANCHOR_FLOOR if ordered else 0.0
    for n, weight in ordered:
        if weight < floor:
            break
        if any(segment_of[m] == segment_of[n] and abs(m - n) < ANCHOR_GAP_LINES for m in picked):
            continue
        picked.append(n)
        if len(picked) == limit:
            break
    return picked


# ---------------------------------------------------------------- ② 범위 뽑기

def ranges_around(anchor: int, lines: list[dict], budget_sec: float) -> list[tuple[int, int]]:
    """핵심 줄 하나 둘레에서 자를 수 있는 (시작, 끝) 범위. 순수 함수.

    시작점: 앞을 가리키지 않고(cutting.needs_lead_in) 앞 발화가 문장을 끝낸 줄 — cutting.lead_in 이
    시작점을 당길 때 쓰는 "좋은 시작점" 과 같은 정의다. 이어진 발화의 첫 줄도 좋은 시작점이다.
    끝점: 문장을 끝내는 줄(cutting.ends_sentence), 또는 이어진 발화의 마지막 줄.

    🔴 **구간 경계는 넘는다. 끊긴 곳은 넘지 않는다.** 범위는 같은 청크의 발화 번호가 1씩 이어지는 동안만
    늘어난다 — 그 안이면 시간상 붙어 있어 조각 안에 점프가 없다. 구간은 주제 경계일 뿐이고 말은 거기서
    끊기지 않는다(ranking.LEAD_IN_SHOULDER 주석과 같은 이야기).
    실측(2026-10-31, 서강대 "교과전형과 지역균형전형의 최저 기준"): 처음엔 같은 구간 안으로 묶었다.
    "이 논술전형 선발할 때 수능최저학력기준 적용합니다." 가 앞 구간의 마지막 발화라 잘려 나가고 뒤의
    "국어, 수학, 영어, 탐구… 7이내" 만 남았다 — **논술 기준이 교과 질문의 1등 후보**가 됐다. 무엇에 대한
    숫자인지 말하는 문장이 빠지면 판정기도 그 숫자가 어느 전형 것인지 알 수 없다.

    🔴 좋은 자리가 없으면 핵심 줄 자체를 쓴다. 어색한 시작은 판정이 잡고 크리에이터가 대사를 읽고
    판단한다 — 범위를 하나도 못 내면 그 자리의 답이 통째로 사라진다.
    """
    by_line = {line["line"]: line for line in lines}
    first, last = anchor, anchor
    while _continues(by_line.get(first - 1), by_line[first]):
        first -= 1
    while _continues(by_line[last], by_line.get(last + 1)):
        last += 1

    starts: list[int] = []
    for n in range(anchor, max(first, anchor - MAX_BACK_LINES) - 1, -1):
        line = by_line[n]
        if cutting.needs_lead_in(line["text"]):
            continue
        if n == first or cutting.ends_sentence(by_line[n - 1]["text"]):
            starts.append(n)
            if len(starts) == STARTS_PER_ANCHOR:
                break
    ends: list[int] = []
    for n in range(anchor, min(last, anchor + MAX_FWD_LINES) + 1):
        if n == last or cutting.ends_sentence(by_line[n]["text"]):
            ends.append(n)
            if len(ends) == ENDS_PER_ANCHOR:
                break

    out: list[tuple[int, int]] = []
    for start in starts or [anchor]:
        for end in ends or [anchor]:
            length = by_line[end]["end_sec"] - by_line[start]["start_sec"]
            if MIN_SEC <= length <= budget_sec:
                out.append((start, end))
    if not out:
        # 예산 안에 드는 조합이 없다(발화 하나가 길거나 문장 끝이 멀다). 핵심 줄 하나를 내고
        # 예산은 enforce_budget 이 발화 단위로 맞춘다.
        out.append((anchor, anchor))
    return out


def _continues(before: dict | None, after: dict | None) -> bool:
    """`after` 가 `before` 의 바로 다음 발화인가 — 같은 청크, 발화 번호가 하나 차이."""
    return (
        before is not None and after is not None
        and before["chunk_id"] == after["chunk_id"]
        and after["utterance_idx"] == before["utterance_idx"] + 1
    )


def build_spans(anchors: list[int], lines: list[dict], budget_sec: float) -> list[Span]:
    """핵심 줄마다 범위를 뽑아 실제 초·대사로 바꾼다. 같은 범위는 하나만. 핵심 줄 순서(무게 순)를 지킨다."""
    spans: list[Span] = []
    seen: set[tuple] = set()
    # 🔴 핵심 줄마다 돌아가며 하나씩 뽑는다. 첫 핵심 줄의 범위로 상한을 다 채우면 나머지 자리가
    # 판정조차 받지 못한다.
    pools = [ranges_around(anchor, lines, budget_sec) for anchor in anchors]
    for round_ in range(max((len(p) for p in pools), default=0)):
        for anchor, pool in zip(anchors, pools):
            if round_ >= len(pool) or len(spans) >= MAX_SPANS:
                continue
            start, end = pool[round_]
            parts = cutting.enforce_budget(
                cutting.resolve_parts([ranking.Part(start, end)], lines), budget_sec, lines
            )
            shape = tuple((p.chunk_id, p.start_utterance_idx, p.end_utterance_idx) for p in parts)
            if shape in seen:
                continue
            seen.add(shape)
            spans.append(Span([(start, end)], anchor, parts))
    return spans


def combos_of(singles: list[Span], lines: list[dict], budget_sec: float) -> list[Span]:
    """단일 범위끼리 짝을 지어 조합 후보를 만든다. 겹치지 않고 예산 안에 드는 것만. 순서는 재료 순서를 따른다."""
    combos: list[Span] = []
    for i, a in enumerate(singles):
        for b in singles[i + 1:]:
            first, second = sorted((a, b), key=lambda s: s.ranges[0][0])
            if second.ranges[0][0] <= first.ranges[0][1]:
                continue
            if first.total_sec + second.total_sec > budget_sec:
                continue
            combos.append(Span(first.ranges + second.ranges, first.anchor, first.parts + second.parts))
    return combos


# ---------------------------------------------------------------- ④ 고르기

def overlaps(a: Span, b: Span) -> bool:
    left, right = a.line_set(), b.line_set()
    return len(left & right) / max(1, min(len(left), len(right))) >= OVERLAP


def select(judged: list[tuple[Span, judge.Judged]], limit: int) -> list[tuple[Span, judge.Judged]]:
    """합격 먼저, 그 안에서 점수 → 답함 확률 순. 이미 고른 것과 많이 겹치면 건너뛴다.

    🔴 전부 떨어져도 비우지 않는다 — answer._pick 과 같은 규칙이다. 사람이 읽고 쓸 만하다고 판단할 수 있다.

    🔴 **하나라도 합격하면, 떨어진 것은 "답은 하는데 자립에서만 떨어진 것" 만 남긴다.** 시작이 조금
    어색한 답은 크리에이터가 읽고 살릴 수 있지만, 답이 아닌 것은 자리만 차지한다(ANCHOR_FLOOR 의 실측).
    """
    ordered = sorted(
        judged,
        key=lambda item: (
            item[1].verdict.passed, item[1].verdict.score, item[1].verdict.p_answers or 0.0,
        ),
        reverse=True,
    )
    any_passed = any(j.verdict.passed for _, j in judged)
    picked: list[tuple[Span, judge.Judged]] = []
    for span, judged_one in ordered:
        if any_passed and not judged_one.verdict.passed and not judged_one.verdict.answers:
            continue
        if any(overlaps(span, other) for other, _ in picked):
            continue
        picked.append((span, judged_one))
        if len(picked) == limit:
            break
    return picked


# ---------------------------------------------------------------- 본체

def propose(
    conn: psycopg.Connection,
    cfg: config.Config,
    question: str,
    segments: list[dict],
    standard: str | None,
    source_id: int | None = None,
    run_id: int | None = None,
) -> Proposal:
    lines = ranking.number_lines(conn, segments)
    if not lines:
        return Proposal(False, "고른 구간에 대사가 없습니다", [], 0, {})

    # ① 위치 찾기 — 🔴 관리자 기준을 넣지 않는다. "어디에 답이 있나" 는 사실 판단이다(routing 과 같은 경계).
    state, questions, covers = locate_request(question, lines)
    answers, usage, latency_ms, raw = jev.system_one(cfg, state, questions)
    located = read_located(answers, covers, lines)
    anchors = anchors_of(located, lines)
    _record(
        conn, cfg, source_id, run_id, "locate", usage, latency_ms,
        json.dumps({"state": state, "questions": questions}, ensure_ascii=False), raw,
        {
            "exists": located.exists,
            "segments": {str(k): round(v, 3) for k, v in located.segment_prob.items()},
            "anchors": [{"line": n, "weight": round(located.line_weight[n], 3)} for n in anchors],
            "lines": len(lines),
        },
    )
    # 🔴 끊는다 — 아래 판정 수십 회가 실패해 롤백해도 이 기록은 남아야 한다(answer.py 의 같은 규칙).
    conn.commit()
    if located.exists < EXISTS_MIN or not anchors:
        return Proposal(
            False,
            f"이 영상에서 질문의 답을 찾지 못했습니다 (Jev 답 존재 확률 {located.exists:.2f})",
            [], 0, {"exists": located.exists},
        )

    # ② 범위 뽑기 → ③ 판정
    budget = cfg.teaser_max_sec
    spans = build_spans(anchors, lines, budget)
    started = time.monotonic()
    judged = _judge_all(conn, cfg, question, spans, standard, source_id, run_id, "span", lines)
    # 조합: **답은 하는** 단일 범위 위쪽 몇 개로 짝을 짓는다. 🔴 자립은 묻지 않는다 — 조합에서는 앞 조각이
    # 뒤 조각의 맥락이 된다. 실측(2026-10-31, 서강대 "취업률 및 계열별 취업 현황"): "인문계열을 졸업한
    # 학생들도 똑같이 높은 취업률을…" 은 답함 0.62 · 자립 0.37 로 떨어진다("~도 똑같이" 가 앞을 가리킨다).
    # 합격한 것만 재료로 쓰면 이 조각이 빠져 질문의 뒤 절반이 사라진다. 답이 아닌 조각은 잇지 않는다.
    # 🔴 재료는 겹침 제거(select) **전**에서, **앵커마다** 고른다. 제거 뒤에는 앵커마다 가장 긴 변형만 남는데,
    # 같은 실측에서 그 둘이 44초 + 33초 = 77초라 60초 예산에 짝이 하나도 안 지어졌다. 그렇다고 전체 위쪽 8개를
    # 쓰면 8개 전부 "취업률 1등" 앵커의 변형이라 서로 겹쳐 역시 짝이 0개였다.
    ranked = sorted(
        judged, key=lambda item: (item[1].verdict.score, item[1].verdict.p_answers or 0.0), reverse=True,
    )
    taken: dict[int, int] = {}
    answering: list[Span] = []
    for span, judged_one in ranked:
        if judged_one.verdict.answers and taken.get(span.anchor, 0) < COMBO_PER_ANCHOR:
            taken[span.anchor] = taken.get(span.anchor, 0) + 1
            answering.append(span)
    combos = combos_of(answering, lines, budget)[:MAX_COMBOS]
    judged += _judge_all(conn, cfg, question, combos, standard, source_id, run_id, "combo", lines)
    judge_ms = int((time.monotonic() - started) * 1000)
    conn.commit()
    if not judged:
        raise judge.JudgeError("판정이 전부 실패했다 — stage_calls.error 를 본다")

    # ④ 고르기
    chosen = select(judged, cfg.answer_candidates)
    scored = [
        judge.Scored(
            ranking.Candidate(
                "combo" if len(span.parts) > 1 else "single",
                _reason(span, located),
                [ranking.Part(start, end) for start, end in span.ranges],
            ),
            span.parts,
            judged_one.verdict,
        )
        for span, judged_one in chosen
    ]
    return Proposal(
        True,
        f"Jev 가 답의 핵심으로 짚은 줄 {len(anchors)}곳 둘레에서 범위 {len(spans)}개 · 조합 {len(combos)}개를 판정했다",
        scored,
        judge_ms,
        {
            "engine": "jev",
            "exists": located.exists,
            "anchors": anchors,
            "judged": len(judged),
            "spans": len(spans),
            "combos": len(combos),
        },
    )


def _reason(span: Span, located: Located) -> str:
    """후보 카드의 "왜 이렇게 잘랐나". 🔴 지어낸 문장이 아니라 실제로 일어난 일을 적는다."""
    where = ", ".join(f"{start}~{end}번 줄" for start, end in span.ranges)
    weight = located.line_weight.get(span.anchor, 0.0)
    return f"답의 핵심으로 짚힌 {span.anchor}번 줄(무게 {weight:.2f}) 둘레 — {where}, {span.total_sec:.0f}초"


def _before(start: int, by_line: dict[int, dict]) -> str | None:
    """범위 바로 앞의 이어진 발화(최대 judge.BEFORE_LINES 개). 끊긴 곳에서 멈춘다 — 점프 너머는 앞 대사가 아니다."""
    texts: list[str] = []
    n = start
    while len(texts) < judge.BEFORE_LINES and _continues(by_line.get(n - 1), by_line.get(n)):
        n -= 1
        texts.insert(0, by_line[n]["text"].strip())
    return " ".join(texts) or None


def _judge_all(
    conn, cfg, question: str, spans: list[Span], standard: str | None,
    source_id: int | None, run_id: int | None, kind: str, lines: list[dict],
) -> list[tuple[Span, judge.Judged]]:
    """병렬 판정 후 **한 곳에서** 기록한다. 🔴 judge_jev 는 DB 를 만지지 않는다 — 그래서 스레드로 돌린다.

    🔴 하나가 실패해도 나머지는 쓴다. 판정 수십 번 중 하나의 일시 장애로 답하기 전체를 버리면 그때까지
    쓴 호출이 통째로 버려진다. 실패는 stage_calls.error 에 남는다.
    """
    if not spans:
        return []

    by_line = {line["line"]: line for line in lines}

    def one(span: Span):
        try:
            return judge.judge_jev(
                cfg, question, [p.text for p in span.parts], standard,
                before=_before(span.ranges[0][0], by_line),
            ), None
        except (jev.JevError, judge.JudgeError) as exc:
            return None, exc

    with ThreadPoolExecutor(max_workers=JUDGE_WORKERS, thread_name_prefix="jev") as pool:
        results = list(pool.map(one, spans))

    out: list[tuple[Span, judge.Judged]] = []
    for span, (judged_one, error) in zip(spans, results):
        params = {
            "kind": kind, "ranges": span.ranges, "anchor": span.anchor,
            "total_sec": round(span.total_sec, 2),
        }
        if judged_one is None:
            conn.execute(
                "insert into stage_calls (source_id, run_id, stage, model, params, error)"
                " values (%s, %s, 'judge', %s, %s, %s)",
                (source_id, run_id, cfg.jev_model, Jsonb(params), f"{type(error).__name__}: {error}"),
            )
            continue
        verdict = judged_one.verdict
        _record(
            conn, cfg, source_id, run_id, "judge", judged_one.usage, judged_one.latency_ms,
            judged_one.prompt, judged_one.raw,
            params | {
                "p_standalone": verdict.p_standalone, "p_answers": verdict.p_answers,
                "score": verdict.score, "passed": verdict.passed,
            },
        )
        out.append((span, judged_one))
    return out


def _record(
    conn, cfg: config.Config, source_id, run_id, stage: str, usage: dict, latency_ms: int,
    prompt: str, raw: str, params: dict,
) -> None:
    conn.execute(
        """insert into stage_calls (source_id, run_id, stage, model, input_tokens, output_tokens,
                                    latency_ms, prompt, response, params)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            source_id, run_id, stage, usage.get("model") or cfg.jev_model,
            usage.get("input_tokens"), usage.get("output_tokens"), latency_ms,
            # 🔴 Gemini 단계와 같은 규칙 — 무엇을 보고 무엇을 답했는지가 없으면 판정이 이상할 때 확인할 길이 없다.
            prompt if cfg.store_prompts else None,
            raw if cfg.store_prompts else None,
            Jsonb(params),
        ),
    )
