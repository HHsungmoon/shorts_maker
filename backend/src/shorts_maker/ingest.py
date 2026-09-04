"""A1 — 소재 등록과 청크 추출.

Source = 원본 영상 전체. Chunk = 그 안에서 실제로 처리할 조각.
개발 초기에는 5분짜리 청크로 루프를 돌리고, A8 에서 같은 소스에 30분 청크를 만든다(문서 §10).

🔴 시간 축은 전부 **소스 절대 초**로 통일한다. 청크 로컬 시간으로 저장하면 [7] 에서
원본을 다시 자를 때(§4-[7]) 매번 변환해야 하고, 한 번만 빠뜨려도 조용히 어긋난다.
"""

import hashlib
import sqlite3
import time
from pathlib import Path

from . import config, ffmpeg


class IngestError(RuntimeError):
    pass


def fingerprint(path: Path) -> str:
    """파일 내용 해시. 같은 영상이 두 번 들어오는 걸 DB 유니크 제약이 막는 근거다(§5)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        # 80분 1080p 면 1GB 대라 한 번에 읽지 않는다.
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def resolve_source_path(cfg: config.Config, raw: str) -> Path:
    """🔴 입력 경로를 SHORTS_SOURCE_DIR 하위로 가둔다.

    지금은 CLI 라 과해 보이지만, C3 에서 관리자 페이지가 같은 함수를 쓴다. 그때 가드가
    없으면 관리자가 준 문자열로 서버의 아무 파일이나 읽히게 된다. 여기 두면 API 가 상속한다.
    """
    root = cfg.source_dir.resolve()
    path = Path(raw).expanduser()
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root):
        raise IngestError(f"입력은 {root} 하위여야 한다: {path}")
    if not path.is_file():
        raise IngestError(f"파일이 없다: {path}")
    return path


def add_source(
    conn: sqlite3.Connection,
    cfg: config.Config,
    raw_path: str,
    title: str,
    content_type: str,
    origin: str | None,
    context: str | None,
    language: str | None = None,
) -> int:
    path = resolve_source_path(cfg, raw_path)
    duration = ffmpeg.duration_sec(str(path))
    try:
        cursor = conn.execute(
            """insert into sources
               (title, content_type, path, duration_sec, origin, fingerprint, context, language)
               values (?, ?, ?, ?, ?, ?, ?, ?)""",
            # 🔴 source_dir 기준 상대경로로 저장한다(config.Config.store_source 주석).
            (title, content_type, cfg.store_source(path), duration, origin, fingerprint(path), context, language),
        )
    except sqlite3.IntegrityError as exc:
        if "fingerprint" in str(exc):
            raise IngestError("같은 내용의 원본이 이미 등록돼 있다") from exc
        raise
    conn.commit()
    return cursor.lastrowid


def add_chunk(conn: sqlite3.Connection, cfg: config.Config, source_id: int, start: float, end: float) -> int:
    row = conn.execute("select path, content_type, duration_sec from sources where id = ?", (source_id,)).fetchone()
    if row is None:
        raise IngestError(f"source {source_id} 없음")
    if row["duration_sec"] and end > row["duration_sec"]:
        raise IngestError(f"end={end} 가 원본 길이 {row['duration_sec']:.0f}s 를 넘는다")

    idx = conn.execute(
        "select coalesce(max(idx) + 1, 0) as next from chunks where source_id = ?", (source_id,)
    ).fetchone()["next"]

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    # LECTURE 는 화면을 보지 않는다(§4-1) — STT 입력만 있으면 되므로 오디오만 뽑는다.
    # 부수 효과가 하나 더 있다: 영상 복사는 키프레임에 스냅돼 시작이 최대 몇 초 밀리는데,
    # 오디오 재인코딩은 샘플 단위로 정확해서 청크 시작 초가 요청값과 일치한다(§12).
    # 🔴 FILM 은 Gemini 에 영상을 넣어야 하므로 여기서 갈라진다.
    if row["content_type"] != "LECTURE":
        raise IngestError(f"{row['content_type']} 청크 추출은 아직 구현 안 됨 (2단계)")

    out = cfg.work_dir / f"source{source_id}_chunk{idx}.wav"
    started = time.monotonic()
    ffmpeg.extract_audio(str(cfg.source_file(row["path"])), str(out), start, end)
    latency_ms = int((time.monotonic() - started) * 1000)

    cursor = conn.execute(
        "insert into chunks (source_id, idx, start_sec, end_sec, path) values (?, ?, ?, ?, ?)",
        (source_id, idx, start, end, cfg.store_work(out)),
    )
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms) values (?, 'chunk', ?)",
        (source_id, latency_ms),
    )
    conn.commit()
    return cursor.lastrowid
