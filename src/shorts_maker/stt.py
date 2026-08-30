"""A3 — STT (문서 §4-[1]).

이 강연에는 사람이 만든 자막이 없고 자동자막은 타임스탬프가 롤링 윈도로 겹쳐 쓸 수 없다
(§1-2 실측). 그래서 STT 가 대안이 아니라 유일한 경로다.

여기서 만든 발화 타임스탬프가 파이프라인 전체의 시간 기준이 된다 — [3]·[6] 의 LLM 은
**발화 인덱스만** 고르고, 초는 언제나 여기서 되찾는다(§12).
"""

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import config


class SttError(RuntimeError):
    pass


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
                "words": json.dumps(words, ensure_ascii=False) if words else None,
                "avg_logprob": getattr(segment, "avg_logprob", None),
                "no_speech_prob": getattr(segment, "no_speech_prob", None),
            }
        )
    return rows, skipped


def transcribe(
    audio_path: Path, chunk_start_sec: float, model_name: str, initial_prompt: str | None = None
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
        # 언어를 고정한다. 자동 감지는 앞부분 30초로 판단해서 가끔 틀리는데, 틀리면 결과가
        # 통째로 쓸모없어진다. 입력을 제한하는 쪽이 낫다(§12).
        language="ko",
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


def run_for_chunk(
    conn: sqlite3.Connection,
    cfg: config.Config,
    chunk_id: int,
    model_name: str | None,
    force: bool,
    initial_prompt: str | None = None,
) -> Transcription:
    chunk = conn.execute(
        "select c.*, s.id as source_id from chunks c join sources s on s.id = c.source_id where c.id = ?",
        (chunk_id,),
    ).fetchone()
    if chunk is None:
        raise SttError(f"chunk {chunk_id} 없음")

    existing = conn.execute(
        "select count(*) as n from utterances where chunk_id = ?", (chunk_id,)
    ).fetchone()["n"]
    if existing and not force:
        raise SttError(f"chunk {chunk_id} 에 이미 발화 {existing}개가 있다 — 다시 하려면 --force")

    audio = Path(chunk["path"])
    if not audio.is_file():
        raise SttError(f"청크 파일이 없다: {audio}")

    result = transcribe(audio, float(chunk["start_sec"]), model_name or cfg.whisper_model, initial_prompt)

    conn.execute("delete from utterances where chunk_id = ?", (chunk_id,))
    conn.executemany(
        """insert into utterances (chunk_id, idx, start_sec, end_sec, text, words, avg_logprob, no_speech_prob)
           values (:chunk_id, :idx, :start_sec, :end_sec, :text, :words, :avg_logprob, :no_speech_prob)""",
        [{**row, "chunk_id": chunk_id} for row in result.rows],
    )
    conn.execute(
        """insert into stage_calls (source_id, stage, model, latency_ms, params)
           values (?, 'stt', ?, ?, ?)""",
        (
            chunk["source_id"],
            f"faster-whisper:{result.model}",
            result.transcribe_ms,
            json.dumps(
                {"initial_prompt": initial_prompt, "vad_filter": True, "language": "ko"},
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    return result
