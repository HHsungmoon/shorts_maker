"""A3 — STT (문서 §4-[1]).

🔴 **2026-11-01 부터 기본 경로가 아니다.** 대사는 유튜브 자막(`captions.py`)이 기본이고, 이건 영상 준비의
두 번째 버튼이다 — 자막 트랙이 없는 영상(화면에 자막을 구운 원티드 영상 등)·로컬 파일용. whisper large-v3 가
**없는 문장을 지어내는 것**을 실측했다(make_shorts §7-1 재측정 2): 흔적은 길이 0초 단어다.
(옛 판단 "자동자막은 롤링 윈도로 겹쳐 쓸 수 없다" 는 VTT 형식의 문제였다 — json3 는 단어 오프셋이 있다.)

여기서 만든 발화 타임스탬프가 파이프라인 전체의 시간 기준이 된다 — [3]·[6] 의 LLM 은
**발화 인덱스만** 고르고, 초는 언제나 여기서 되찾는다(§12).
"""

import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import ffmpeg
from ..db import store
from . import transcripts


# whisper 가 아는 코드 중 이 프로젝트가 쓰는 것만 연다. 늘리려면 여기 한 줄이면 된다.
LANGUAGES = {"ko": "한국어", "en": "영어", "ja": "일본어", "zh": "중국어"}


class SttError(RuntimeError):
    pass


# 용어 힌트(initial_prompt) 상한. 🔴 whisper 는 힌트의 **마지막 223 토큰만** 쓰고 앞은 말없이 버린다
# (faster-whisper: max_length // 2 - 1). 실측(2026-10-24, whisper small 토크나이저): 서강대 용어 14개
# 101자 = 96 토큰, 한글은 글자당 약 0.95 토큰이다. 쉼표·공백까지 200자면 190 토큰 안팎이라 잘리지 않는다.
# 넘치면 거절한다 — 받아 두고 앞쪽 용어를 조용히 버리면 "넣었는데 왜 안 먹지" 가 된다.
TERMS_MAX_CHARS = 200
# 용어 하나의 상한. 고유명사 하나가 이보다 길면 문장을 넣은 것이다.
TERM_MAX_CHARS = 40


def normalize_terms(raw: list[str]) -> list[str]:
    """화면에서 온 용어 목록을 정리한다 — 앞뒤 공백 · 빈 칸 · 중복을 걷고 순서는 지킨다.

    🔴 쉼표를 넣을 수 없다. 힌트는 용어를 쉼표로 이어 붙인 한 줄이라 용어 안의 쉼표는 용어를 둘로 쪼갠다.
    """
    terms: list[str] = []
    for item in raw:
        term = " ".join(str(item).split())
        if not term or term in terms:
            continue
        if "," in term:
            raise SttError(f"용어에 쉼표를 넣을 수 없습니다: {term}")
        if len(term) > TERM_MAX_CHARS:
            raise SttError(f"용어 하나는 {TERM_MAX_CHARS}자까지입니다: {term[:20]}…")
        terms.append(term)
    if len(terms_prompt(terms) or "") > TERMS_MAX_CHARS:
        raise SttError(
            f"용어 힌트는 모두 합쳐 {TERMS_MAX_CHARS}자까지입니다 — 넘치면 음성 인식이 앞쪽 용어를 버립니다"
        )
    return terms


def terms_prompt(terms: list[str]) -> str | None:
    """용어 목록 → initial_prompt. 비어 있으면 None(힌트 없음)."""
    return ", ".join(terms) or None


# 교정 사전(sources.stt_fixes) 상한. 한 항목은 잘못 들린 낱말 하나 — 문장을 바꾸는 자리가 아니다.
FIX_MAX_CHARS = 40
FIXES_MAX = 100


def normalize_fixes(raw: list[dict]) -> list[dict]:
    """화면에서 온 교정 목록을 정리한다 — 앞뒤 공백을 걷고, 빈 줄은 버리고, 순서는 지킨다.

    🔴 **몇 번을 다시 적용해도 결과가 같아야 한다.** 저장할 때마다 전사 전체에 다시 적용하기 때문이다
    (apply_fixes_to_source). 그래서 둘을 거절한다:
      - 바른 말 안에 잘못 들린 말이 들어 있는 것: "최저" → "수능최저" 는 적용할 때마다 "수능수능…" 이 된다
      - 한 항목의 바른 말이 다른 항목의 잘못 들린 말을 품는 것: 순서에 따라 결과가 달라진다
    """
    fixes: list[dict] = []
    for item in raw:
        wrong = " ".join(str(item.get("from", "")).split())
        right = " ".join(str(item.get("to", "")).split())
        if not wrong and not right:
            continue
        if not wrong or not right:
            raise SttError("잘못 들린 말과 바른 말을 둘 다 적어야 합니다")
        if len(wrong) > FIX_MAX_CHARS or len(right) > FIX_MAX_CHARS:
            raise SttError(f"교정 한 줄은 {FIX_MAX_CHARS}자까지입니다: {wrong[:20]}…")
        if wrong == right:
            continue
        if wrong in right:
            raise SttError(f"바른 말 안에 잘못 들린 말이 들어 있으면 고칠 때마다 늘어납니다: {wrong} → {right}")
        if any(f["from"] == wrong for f in fixes):
            raise SttError(f"같은 말을 두 번 고칠 수 없습니다: {wrong}")
        fixes.append({"from": wrong, "to": right})
    if len(fixes) > FIXES_MAX:
        raise SttError(f"교정은 {FIXES_MAX}개까지입니다")
    for fix in fixes:
        for other in fixes:
            if other is not fix and other["from"] in fix["to"]:
                raise SttError(
                    f"'{fix['to']}' 안에 다른 교정의 '{other['from']}' 이 들어 있어 순서에 따라 결과가 달라집니다"
                )
    return fixes


def fix_words(words: list[dict], wrong: str, right: str) -> list[dict]:
    """단어 타임스탬프 목록에서 `wrong` 을 `right` 로 바꾼다. 원본 목록은 건드리지 않는다.

    자막은 이 목록을 그대로 이어 붙여 만든다(subtitles.build_cues) — `text` 만 고치면 화면 자막은 틀린
    그대로다. 🔴 잘못 들린 말이 **여러 단어에 걸치면** 그 단어들을 하나로 합친다: 첫 단어의 시작부터 끝
    단어의 끝까지. 단어를 지우고 시각을 비우면 자막에 구멍이 나고, 시각을 나누면 우리가 지어낸 초가 된다.
    """
    out = list(words)
    at = 0
    # 왼쪽부터 하나씩 바꾸고 매번 다시 센다. 한 단어 안에 두 번 걸리는 경우("체저체저")가 있어서
    # 위치를 미리 다 구해 두면 앞의 교체가 뒤의 결과를 덮는다. 발화 하나의 단어는 수십 개라 다시 세도 싸다.
    while True:
        joined = "".join(w["text"] for w in out)
        at = joined.find(wrong, at)
        if at == -1:
            return out
        starts = []  # 단어마다 이은 문자열에서의 시작 위치
        offset = 0
        for w in out:
            starts.append(offset)
            offset += len(w["text"])
        first = max(i for i, s in enumerate(starts) if s <= at)
        end = at + len(wrong)
        last = max(i for i, s in enumerate(starts) if s < end)
        text = out[first]["text"][:at - starts[first]] + right + out[last]["text"][end - starts[last]:]
        out[first:last + 1] = [{**out[first], "text": text, "end": out[last]["end"]}]
        # 바꿔 넣은 말 뒤부터 찾는다 — 바른 말 안은 다시 보지 않는다.
        at += len(right)


def apply_fixes(row: dict, fixes: list[dict]) -> dict:
    """발화 하나에 교정을 적용한 새 행. `text` 와 `words` 를 같이 고친다.

    둘은 따로 고친다 — whisper 의 `text` 와 단어를 이은 문자열은 앞 공백 정도가 다를 뿐 같은 말이라
    같은 교정이 양쪽에 똑같이 걸린다.
    """
    text = row["text"]
    words = row.get("words")
    for fix in fixes:
        text = text.replace(fix["from"], fix["to"])
        if words:
            words = fix_words(words, fix["from"], fix["to"])
    return {**row, "text": text, "words": words}


def apply_fixes_to_source(conn: psycopg.Connection, source_id: int, fixes: list[dict]) -> int:
    """이미 있는 대사 전체에 교정을 적용한다. 바뀐 **쓰는 대사** 발화 수를 돌려준다. 커밋은 부른 쪽이 한다.

    지금 쓰는 대사(utterances)와 보관한 두 대사(transcripts)에 모두 건다 — 보관한 쪽을 빼면 [이 대사로 쓰기]
    로 옮겨 담는 순간 교정이 풀리고, 비교 화면이 사전으로 이미 고친 차이를 계속 보여 준다.
    🔴 이미 렌더한 클립의 자막은 바뀌지 않는다 — 영상에 구워져 있다. 발화 번호(idx)와 시각은 그대로라
    구간·클립이 가리키는 범위는 깨지지 않는다.
    🔴 교정을 **빼도** 이미 고친 글자는 되돌아가지 않는다. 원래 글자를 따로 남기지 않는다.
    """
    if not fixes:
        return 0
    targets = (
        # 테이블 이름은 이 상수에서만 온다(사용자 입력 아님).
        ("utterances", """select u.id, u.text, u.words from utterances u join chunks c on c.id = u.chunk_id
                          where c.source_id = %s"""),
        ("transcripts", "select id, text, words from transcripts where source_id = %s"),
    )
    counted = 0
    for table, query in targets:
        changed = []
        for row in conn.execute(query, (source_id,)).fetchall():
            fixed = apply_fixes(dict(row), fixes)
            if fixed["text"] != row["text"] or fixed["words"] != row["words"]:
                changed.append({"id": row["id"], "text": fixed["text"],
                                "words": Jsonb(fixed["words"]) if fixed["words"] else None})
        if changed:
            with conn.cursor() as cur:
                cur.executemany(f"update {table} set text = %(text)s, words = %(words)s where id = %(id)s", changed)
        if table == "utterances":
            counted = len(changed)
    return counted


def check_language(code: str | None) -> str | None:
    """None 은 자동 감지다. 🔴 자동은 앞 30초로 판단해서 가끔 틀리고, 틀리면 결과가 통째로
    쓸모없어진다 — 아는 언어면 지정하는 쪽이 낫다."""
    if code is None or code == "":
        return None
    if code not in LANGUAGES:
        raise SttError(f"지원하지 않는 언어: {code} (가능: {', '.join(LANGUAGES)}, 비우면 자동)")
    return code


@dataclass
class Transcription:
    rows: list[dict]
    skipped: int
    load_ms: int
    transcribe_ms: int
    language: str
    language_probability: float
    model: str


def to_utterance_rows(segments, chunk_start_sec: float) -> tuple[list[dict], int]:
    """whisper 출력을 utterances 행으로 바꾼다.

    🔴 whisper 가 주는 초는 **청크 로컬**이다. 여기서 소스 절대 초로 올린다 — 이 변환을
    한 곳에 가둬야 나중에 [7] 이 원본에서 자를 때 어긋나지 않는다.

    길이가 0 이하거나 텍스트가 빈 발화는 버린다. whisper 는 무음 구간에서 이런 걸 가끔
    뱉는데, 그대로 넣으면 utterances 의 check 제약에 걸려 STT 전체가 죽는다.
    """
    rows: list[dict] = []
    skipped = 0
    for segment in segments:
        text = (getattr(segment, "text", "") or "").strip()
        start = chunk_start_sec + float(segment.start)
        end = chunk_start_sec + float(segment.end)
        if not text or end <= start:
            skipped += 1
            continue
        words = [
            {
                "start": round(chunk_start_sec + float(w.start), 3),
                "end": round(chunk_start_sec + float(w.end), 3),
                "text": w.word,
                "probability": round(float(w.probability), 4),
            }
            for w in (getattr(segment, "words", None) or [])
        ]
        rows.append(
            {
                "idx": len(rows),
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "text": text,
                # 순수 파이썬 리스트로 둔다. jsonb 래핑(Jsonb)은 insert 시점(transcripts.store_rows)에서 — 이 함수는 DB 를 모른다.
                "words": words if words else None,
                "avg_logprob": getattr(segment, "avg_logprob", None),
                "no_speech_prob": getattr(segment, "no_speech_prob", None),
            }
        )
    return rows, skipped


def transcribe(
    audio_path: Path,
    chunk_start_sec: float,
    model_name: str,
    initial_prompt: str | None = None,
    language: str | None = None,
) -> Transcription:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SttError("faster-whisper 가 없다 — `uv sync --extra stt`") from exc

    # CTranslate2 에는 Metal 백엔드가 없다. Apple Silicon 에서도 CPU 로 돈다.
    # int8 은 CPU 에서 float32 보다 몇 배 빠르고 한국어 품질 저하가 크지 않다(§9-8 에서 실측).
    started = time.monotonic()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    load_ms = int((time.monotonic() - started) * 1000)

    started = time.monotonic()
    segments, info = model.transcribe(
        str(audio_path),
        # 원본에 지정된 언어. None 이면 whisper 가 자동 감지한다 — 앞 30초로 판단해서
        # 가끔 틀리고, 틀리면 결과가 통째로 쓸모없어지므로 아는 경우엔 지정하는 쪽이 낫다.
        language=language,
        # [6] 이 발화 중간을 자르게 될 때 필요하다(§4-[6]).
        word_timestamps=True,
        # 강연은 침묵 구간이 길고, whisper 는 무음에서 같은 문장을 반복 생성하는 환각이 있다.
        # VAD 로 무음을 먼저 걷어내면 그 실패가 크게 준다.
        vad_filter=True,
        # 도메인 어휘를 미리 물려준다. 이 강연은 고유명사가 계속 나오는데 whisper 가
        # '니체'를 '이체/닐체'로 흘린다 — 그 단어가 [5] rank 의 판단 재료라 그냥 둘 수 없다.
        # 🔴 프롬프트가 길면 모델이 그 문체를 따라 하며 없는 말을 만든다. 어휘 나열로 짧게.
        initial_prompt=initial_prompt,
    )
    # segments 는 제너레이터다 — 여기서 소비될 때 실제 추론이 돈다.
    rows, skipped = to_utterance_rows(segments, chunk_start_sec)
    transcribe_ms = int((time.monotonic() - started) * 1000)

    return Transcription(
        rows=rows,
        skipped=skipped,
        load_ms=load_ms,
        transcribe_ms=transcribe_ms,
        language=info.language,
        language_probability=round(float(info.language_probability), 4),
        model=model_name,
    )


# 이보다 짧게 남은 범위는 듣지 않는다. 청크 경계 반올림(밀리초)이 "안 들은 범위" 로 남는 것을 막는다.
MIN_SPAN_SEC = 1.0


def uncovered(start: float, end: float, heard: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[start, end) 에서 이미 들은 범위를 뺀 나머지. MIN_SPAN_SEC 보다 짧은 조각은 버린다."""
    left = [(start, end)]
    for a, b in sorted(heard):
        nxt = []
        for x, y in left:
            if b <= x or a >= y:
                nxt.append((x, y))
                continue
            if a > x:
                nxt.append((x, a))
            if b < y:
                nxt.append((b, y))
        left = nxt
    return [(x, y) for x, y in left if y - x >= MIN_SPAN_SEC]


def remaining(conn: psycopg.Connection, source_id: int) -> list[dict]:
    """아직 안 들은 범위. 청크별로 `{chunk_id, start, end, whole}` — `whole` 이면 청크 오디오를 그대로 쓴다.

    🔴 **한 번 들은 오디오는 다시 듣지 않는다**(사용자 결정 2026-11-01, tease §5-1). 이 목록이 비면 [음성 인식] 은
    끝난 것이다. 분석 범위를 넓혀 다시 추출하면 새로 생긴 부분만 여기 나온다.
    """
    heard = [(r["start_sec"], r["end_sec"]) for r in conn.execute(
        "select start_sec, end_sec from stt_spans where source_id = %s", (source_id,)
    )]
    plan = []
    for chunk in conn.execute(
        "select id, start_sec, end_sec from chunks where source_id = %s order by idx", (source_id,)
    ).fetchall():
        for a, b in uncovered(chunk["start_sec"], chunk["end_sec"], heard):
            plan.append({
                "chunk_id": chunk["id"], "start": a, "end": b,
                "whole": a == chunk["start_sec"] and b == chunk["end_sec"],
            })
    return plan


def run_for_source(
    conn: psycopg.Connection,
    cfg: config.Config,
    source_id: int,
    model_name: str | None = None,
    initial_prompt: str | None = None,
    language: str | None = None,
) -> list[Transcription]:
    """**아직 안 들은 범위**를 순서대로 전사한다. 화면에서 [음성 인식] 한 번이 이것이다.

    🔴 같은 오디오는 한 번뿐이다 — 다 들었으면 SttError. 다시 돌리는 인자(force)는 없다.
    🔴 범위를 하나씩 도는 이유는 메모리다 — 95분을 한 번에 돌리면 컨테이너 한도를 넘어 죽는다
    (ingest.add_chunk 주석). 범위는 청크보다 길지 않다.

    결과는 `transcripts`(whisper)에 **덧붙이고** 들은 범위를 `stt_spans` 에 남긴다. 지금 쓰는 대사가 유튜브
    자막이면 그대로 둔다(비교 화면이 둘을 맞댄다). 자막이 아니면(없거나 이미 음성 인식) 범위 하나가 끝날
    때마다 음성 인식 대사를 쓰는 대사로 옮겨 담는다 — 화면이 조각별 진행을 본다.
    중간에 실패하면 거기서 멈춘다. 끝난 범위는 이미 커밋돼 있어 다시 누르면 남은 범위만 한다.
    """
    if not conn.execute("select 1 from chunks where source_id = %s limit 1", (source_id,)).fetchone():
        raise SttError(f"source {source_id} 에 청크가 없다 — 먼저 구간을 추출한다")
    # 힌트를 따로 주지 않으면 원본에 저장된 용어 목록을 쓴다(화면의 "용어 힌트"). CLI `--prompt` 는 덮어쓴다.
    if initial_prompt is None:
        row = conn.execute("select stt_terms from sources where id = %s", (source_id,)).fetchone()
        initial_prompt = terms_prompt(list(row["stt_terms"])) if row else None
    # 🔴 도는 동안 재분할이 들어오면 청크가 사라져 외래키 위반으로 죽는다(store.source_lock).
    with store.source_lock(conn, source_id, "전사"):
        plan = remaining(conn, source_id)
        if not plan:
            raise SttError("이 원본은 음성 인식을 이미 했습니다 — 같은 오디오는 다시 돌리지 않습니다")
        return [
            run_span(conn, cfg, source_id, span, model_name, initial_prompt, language) for span in plan
        ]


def run_span(
    conn: psycopg.Connection,
    cfg: config.Config,
    source_id: int,
    span: dict,
    model_name: str | None,
    initial_prompt: str | None = None,
    language: str | None = None,
) -> Transcription:
    """범위 하나를 듣는다. `span` 은 `remaining()` 의 한 항목."""
    chunk = conn.execute(
        "select c.*, s.language, s.path as source_path, s.caption_source"
        " from chunks c join sources s on s.id = c.source_id where c.id = %s",
        (span["chunk_id"],),
    ).fetchone()
    if chunk is None:
        raise SttError(f"chunk {span['chunk_id']} 없음")

    # 이번 호출에 언어를 주면 원본 설정을 덮고 그 값을 저장한다 — 처음에 잘못 골랐을 때
    # 원본을 다시 등록하지 않고 고칠 수 있어야 한다.
    chosen = check_language(language) if language is not None else chunk["language"]
    if chosen != chunk["language"]:
        conn.execute("update sources set language = %s where id = %s", (chosen, source_id))
        conn.commit()

    # 🔴 STT 는 몇 분 돈다. 트랜잭션을 열어둔 채 몇 분 계산하지 않는다 — 위 읽기로 열린 트랜잭션을 여기서 끊는다.
    conn.commit()
    extracted: Path | None = None
    if span["whole"]:
        audio = cfg.work_file(chunk["path"])
        if not audio.is_file():
            raise SttError(f"청크 파일이 없다: {audio}")
    else:
        # 청크 일부만 남았다(범위를 넓혀 다시 추출했거나, 옛 범위와 겹친다). 남은 부분만 원본에서 뽑는다 —
        # 들은 초를 다시 듣지 않으려고. 오디오 재인코딩이라 시작이 샘플 단위로 정확하다(ingest.add_chunk).
        extracted = cfg.work_dir / f"source{source_id}_stt_{span['start']:.0f}_{span['end']:.0f}.wav"
        ffmpeg.extract_audio(str(cfg.source_file(chunk["source_path"])), str(extracted), span["start"], span["end"])
        audio = extracted
    try:
        result = transcribe(audio, float(span["start"]), model_name or cfg.whisper_model, initial_prompt, chosen)
    finally:
        if extracted is not None:
            extracted.unlink(missing_ok=True)

    # 교정 사전은 **전사가 끝난 뒤에** 읽는다. 몇 분 도는 사이 화면에서 교정을 저장하면 그 저장은 이미 있는
    # 발화에만 걸리고(apply_fixes_to_source) 지금 전사 중인 조각은 빠진다 — 여기서 새로 읽어야 그 틈이 없다.
    fixes = conn.execute("select stt_fixes from sources where id = %s", (source_id,)).fetchone()["stt_fixes"]
    rows = [apply_fixes(row, fixes) for row in result.rows]
    fixed = sum(1 for before, after in zip(result.rows, rows) if before["text"] != after["text"])

    transcripts.store_rows(conn, source_id, "whisper", rows, replace=False)
    conn.execute(
        """insert into stt_spans (source_id, start_sec, end_sec, model, language, initial_prompt)
           values (%s, %s, %s, %s, %s, %s)""",
        (source_id, span["start"], span["end"], f"faster-whisper:{result.model}", chosen, initial_prompt),
    )
    # 쓰는 대사가 유튜브 자막이면 건드리지 않는다(tease §5-1). 아니면 음성 인식이 쓰는 대사다.
    current = conn.execute("select caption_source from sources where id = %s", (source_id,)).fetchone()
    used = None
    if current["caption_source"] != "youtube":
        used = transcripts.use(conn, source_id, "whisper")
    conn.execute(
        """insert into stage_calls (source_id, stage, model, latency_ms, params)
           values (%s, 'stt', %s, %s, %s)""",
        (
            source_id,
            f"faster-whisper:{result.model}",
            result.transcribe_ms,
            # chunk_id: 어느 조각을 이 설정으로 돌렸나. 없으면 일부 조각만 다시 인식했을 때 화면이
            # "마지막 설정" 을 전 조각의 설정처럼 보여주게 된다(2026-10-24).
            Jsonb({
                "initial_prompt": initial_prompt,
                "vad_filter": True,
                "language": chosen or "auto",
                "chunk_id": span["chunk_id"],
                # 들은 범위(원본 절대 초). 청크 전체가 아닐 수 있다.
                "span": [span["start"], span["end"]],
                "utterances": len(rows),
                # 교정 사전으로 고친 발화 수. 0 이 아니면 저장된 글자는 whisper 원문이 아니다.
                "fixed": fixed,
                # 쓰는 대사로 옮겨 담았나. 자막을 쓰는 중이면 false — 비교용으로만 보관했다.
                "used": used is not None,
            }),
        ),
    )
    conn.commit()
    return result
