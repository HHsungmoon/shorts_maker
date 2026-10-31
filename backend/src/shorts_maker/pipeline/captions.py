"""[1] 대사 확보 — 유튜브 자막 (tease §5-1, 2026-11-01).

🔴 **기본 경로다.** 음성 인식(`stt.py`)은 영상 준비의 두 번째 버튼으로 남는다. 자동으로 넘어가지 않는다 —
자막이 없으면 그렇다고 알리고 사람이 [음성 인식] 을 누른다.

왜 자막이 기본인가(make_shorts §7-1 재측정 2): 글자 정확도는 whisper large-v3 와 비슷했는데(둘 다 약 9% 불일치,
대부분 표기 차이) whisper 만 **없는 문장을 지어냈다** — 31분에 세 곳, 그중 둘은 뜻이 반대. 자막은 말을
빠뜨리기는 해도 지어내지는 않았다. 그리고 31분 영상이 20분이 아니라 1초다.

여기서 만드는 행은 whisper 경로와 **같은 모양**이다(`utterances` · 단어 `words`). 뒤 단계(구간 분할·컷·
번인 자막·판정)는 대사가 어디서 왔는지 모른다. 다른 것은 `avg_logprob`·`no_speech_prob`·단어 `probability`
가 없다는 것뿐이다 — 자막은 그 값을 주지 않고, 지어내지 않는다.
"""

import re
import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import ytdlp
from ..db import store
from . import stt, transcripts

# 발화 하나를 이 이상 이어 붙이지 않는다. whisper 발화는 대부분 2~10초다 — 같은 결이어야 구간 분할 프롬프트의
# 발화 번호가 비슷한 밀도로 찍히고, 컷이 발화 경계로 잘릴 때 한 덩어리가 너무 크지 않다.
MAX_UTTERANCE_SEC = 20.0

# 단어 사이가 이만큼 비면 말이 끊긴 것이다. 자동자막은 문장부호를 대체로 찍지만 빠뜨리는 곳이 있다.
GAP_SEC = 1.5

# 이 글자로 끝나는 단어에서 문장이 끝난다.
SENTENCE_END = re.compile(r"[.?!。？！]$")

# 🔴 혼자 선 군말만 뺀다. 자막은 whisper 와 달리 "어·음" 을 그대로 적는다(서강대 5분에 "어" 172개) —
# 번인 자막에 그대로 찍히고 판정 문장도 지저분해진다. "네·예" 는 **남긴다** — 질문에 대한 대답이다.
FILLERS = {"어", "음", "으", "아", "에", "흠", "엄"}

# [음악] · [웃음] · [박수] 같은 소리 표시. 대사가 아니다.
SOUND_TAG = re.compile(r"^\[[^\]]*\]$")

# 문장부호를 떼고 군말인지 본다("어," · "음.").
_PUNCT = re.compile(r"[.,?!…~·\s]")


class CaptionError(RuntimeError):
    """사람이 고칠 수 있는 이유로 가져올 수 없다. 메시지를 그대로 화면에 보인다."""


def to_utterances(words: list[ytdlp.CaptionWord]) -> tuple[list[dict], dict[str, int]]:
    """자막 단어 → 발화 행(시각은 원본 절대 초). 뺀 것의 이유별 개수를 같이 돌려준다.

    끊는 곳: 문장부호로 끝난 단어 뒤 · 말하는 사람이 바뀐 단어 앞 · 1.5초 넘는 틈 · 20초.
    """
    utterances: list[dict] = []
    dropped = {"filler": 0, "sound": 0}
    current: list[dict] = []
    # 군말을 뺄 때 그 군말이 문장을 끝냈거나("어.") 말하는 사람을 바꿨으면 그 표시는 살린다.
    pending_break = False

    def flush() -> None:
        if not current:
            return
        text = re.sub(r"\s+", " ", "".join(w["text"] for w in current)).strip()
        if text:
            utterances.append({
                "start_sec": current[0]["start"],
                "end_sec": current[-1]["end"],
                "text": text,
                "words": list(current),
                "avg_logprob": None,
                "no_speech_prob": None,
            })
        current.clear()

    for word in words:
        bare = word.text.strip()
        if SOUND_TAG.match(bare):
            dropped["sound"] += 1
            pending_break = True
            continue
        if _PUNCT.sub("", bare) in FILLERS:
            dropped["filler"] += 1
            pending_break = pending_break or word.speaker_change or bool(SENTENCE_END.search(bare))
            continue
        if current and (
            pending_break
            or word.speaker_change
            or word.start - current[-1]["end"] > GAP_SEC
            or word.end - current[0]["start"] > MAX_UTTERANCE_SEC
        ):
            flush()
        pending_break = False
        # whisper 단어처럼 앞에 공백 하나를 둔다 — 번인 자막·교정 사전이 단어를 이어 붙일 때 띄어쓰기가 된다.
        current.append({"start": word.start, "end": word.end, "text": " " + bare})
        if SENTENCE_END.search(bare):
            flush()
    flush()
    return utterances, dropped


def run_for_source(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> dict:
    """유튜브 자막을 받아 보관하고 **지금 쓰는 대사로** 삼는다. 화면의 [유튜브 자막] 한 번.

    자막은 받을 때마다 통째로 갈아 끼운다(1초). 🔴 음성 인식 대사는 건드리지 않는다 — `transcripts` 에 따로
    남아 비교 화면과 [음성 인식 대사로 쓰기] 가 쓴다. 그래서 force 가 없다: 이 버튼은 아무것도 잃게 하지 않는다.
    자막은 **영상 전체**를 보관한다 — 분석 범위를 바꿔도 다시 받지 않고 옮겨 담기만 하면 된다.
    """
    source = conn.execute(
        "select youtube_id, language from sources where id = %s", (source_id,)
    ).fetchone()
    if source is None:
        raise CaptionError(f"source {source_id} 없음")
    if not source["youtube_id"]:
        raise CaptionError("유튜브 영상 id 가 없는 원본입니다. 원본 칸에서 유튜브 주소를 넣거나 [음성 인식] 을 쓰세요")
    if not conn.execute("select 1 from chunks where source_id = %s limit 1", (source_id,)).fetchone():
        raise CaptionError("구간 추출이 먼저입니다 — 분석할 범위를 정해야 그 안의 대사만 담습니다")
    # 원본 언어로 받는다. 비어 있으면(자동 감지로 등록) 한국어 — 지금 다루는 영상이 전부 한국어다.
    language = source["language"] or "ko"

    # 🔴 전사·재분할과 겹치지 않게(store.source_lock). 받는 건 1초지만 그 사이 청크가 지워지면 외래키로 죽는다.
    with store.source_lock(conn, source_id, "대사 가져오기"):
        # 🔴 네트워크 앞에서 트랜잭션을 끊는다(규약).
        conn.commit()
        started = time.monotonic()
        path = None
        try:
            path = ytdlp.fetch_captions(cfg, source["youtube_id"], language)
            words = ytdlp.parse_json3(path)
        except ytdlp.DownloadError as exc:
            _record(conn, source_id, started, {"language": language}, f"{type(exc).__name__}: {exc}")
            conn.commit()
            raise
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

        utterances, dropped = to_utterances(words)
        # 교정 사전은 자막에도 똑같이 건다. 자막이 틀리는 방식(학보님·산발·체자)이 바로 사전이 받는 것이다.
        fixes = conn.execute("select stt_fixes from sources where id = %s", (source_id,)).fetchone()["stt_fixes"]
        corrected = [stt.apply_fixes(row, fixes) for row in utterances]
        fixed = sum(1 for before, after in zip(utterances, corrected) if before["text"] != after["text"])

        transcripts.store_rows(conn, source_id, "youtube", corrected, replace=True)
        used = transcripts.use(conn, source_id, "youtube")
        result = {
            "language": language,
            "words": len(words),
            # 영상 전체에서 만든 발화 수. 그중 분석 범위 안에 든 것이 utterances.
            "captions": len(corrected),
            "utterances": used["utterances"],
            "outside": used["outside"],
            "fillers": dropped["filler"],
            "sounds": dropped["sound"],
            "fixed": fixed,
            # 대사를 갈아 끼워 발화 번호를 시각으로 다시 맞춘 구간·클립 조각 수(transcripts.remap).
            "remapped_segments": used["segments"],
            "remapped_clip_parts": used["clip_parts"],
        }
        _record(conn, source_id, started, result, None)
        conn.commit()
        return result


def _record(conn: psycopg.Connection, source_id: int, started: float, params: dict, error: str | None) -> None:
    # 🔴 규약: 모든 단계를 stage_calls 에. LLM 이 아니라 model·토큰은 null 이다.
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms, params, error) values (%s, 'caption', %s, %s, %s)",
        (source_id, int((time.monotonic() - started) * 1000), Jsonb(params), error),
    )
