"""A1 — 소재 등록과 청크 추출.

Source = 원본 영상 전체. Chunk = 그 안에서 실제로 처리할 조각.
개발 초기에는 5분짜리 청크로 루프를 돌리고, A8 에서 같은 소스에 30분 청크를 만든다(문서 §10).

🔴 시간 축은 전부 **소스 절대 초**로 통일한다. 청크 로컬 시간으로 저장하면 [7] 에서
원본을 다시 자를 때(§4-[7]) 매번 변환해야 하고, 한 번만 빠뜨려도 조용히 어긋난다.

원본 등록은 두 단계다 — `begin_source`(행을 RUNNING 으로 먼저 만든다) → `finish_source`
(파일이 준비되면 길이·지문을 채우고 DONE). 다운로드는 수 분이 걸리는데, 그동안 행이 없으면
화면에는 아무것도 안 보이고 서버가 죽었을 때 `server.serve` 의 "RUNNING 정리" 도 할 일이 없다.
`add_source` 는 파일이 이미 있을 때(CLI·동기 API) 둘을 이어 부르는 편의 함수다.
"""

import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .. import config
from ..adapters import ffmpeg
from . import media


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


@dataclass
class Begun:
    source_id: int
    # DONE 행이 이미 그 경로에 있어서 새로 만들지 않았다. 호출자는 finish 를 부르지 않는다.
    reused: bool


def begin_source(
    conn: psycopg.Connection,
    cfg: config.Config,
    path: Path,
    title: str,
    content_type: str,
    origin: str | None,
    context: str | None,
    language: str | None = None,
) -> Begun:
    """원본 행을 **파일이 준비되기 전에** RUNNING 으로 만든다.

    같은 경로의 행이 이미 있으면: DONE 이면 그대로 재사용(`reused=True`), 그 외(FAILED · 죽은
    RUNNING · PENDING)는 이전 시도의 잔해라 같은 행을 다시 쓴다 — 지우고 새로 만들면 id 가
    바뀌어 화면이 가리키던 곳이 사라진다.

    지문은 파일이 있어야 계산되므로 여기서는 `pending:` 임시값을 넣는다. not null unique 제약을
    지키면서 자리를 잡아두는 용도고, finish_source 가 진짜 값으로 바꾼다.
    """
    existing_id = media.find_source_id(conn, cfg, path.resolve())
    if existing_id is not None:
        status = conn.execute("select status from sources where id = %s", (existing_id,)).fetchone()["status"]
        if status == "DONE":
            return Begun(existing_id, reused=True)
        conn.execute(
            """update sources set title = %s, content_type = %s, origin = %s, context = %s, language = %s,
               status = 'RUNNING', error = null, updated_at = now() where id = %s""",
            (title, content_type, origin, context, language, existing_id),
        )
        conn.commit()
        return Begun(existing_id, reused=False)

    cursor = conn.execute(
        """insert into sources (title, content_type, path, origin, fingerprint, context, language, status)
           values (%s, %s, %s, %s, %s, %s, %s, 'RUNNING') returning id""",
        # 🔴 source_dir 기준 상대경로로 저장한다(config.Config.store_source 주석).
        (title, content_type, cfg.store_source(path), origin, f"pending:{uuid.uuid4().hex}", context, language),
    )
    source_id = cursor.fetchone()["id"]
    conn.commit()
    return Begun(source_id, reused=False)


def fail_source(conn: psycopg.Connection, source_id: int, error: str) -> None:
    conn.execute(
        "update sources set status = 'FAILED', error = %s, updated_at = now() where id = %s",
        (error[:1000], source_id),
    )
    conn.commit()


def finish_source(conn: psycopg.Connection, cfg: config.Config, source_id: int, path: Path) -> None:
    """파일이 준비된 뒤 길이·지문을 채우고 DONE 으로. 실패하면 FAILED 로 남기고 다시 던진다.

    예외: 지문이 이미 다른 행에 있으면(같은 내용을 다른 파일명으로 두 번 넣었다) 이 행은 존재할
    이유가 없어 **지운다**. FAILED 로 남기면 목록에 실체 없는 행이 생기고, 그 파일을 지우려는
    순간 정상 행의 파생물까지 헷갈린다.
    """
    try:
        # 🔴 지문은 1GB 를 끝까지 읽어 몇 분이 걸린다. 트랜잭션을 열어둔 채 몇 분 계산하지 않는다 —
        # 앞선 읽기로 열린 트랜잭션을 여기서 끊는다.
        conn.commit()
        duration = ffmpeg.duration_sec(str(path))
        digest = fingerprint(path)
        conn.execute(
            """update sources set duration_sec = %s, fingerprint = %s, status = 'DONE', error = null,
               updated_at = now() where id = %s""",
            (duration, digest, source_id),
        )
        conn.commit()
    except psycopg.IntegrityError as exc:
        # 🔴 Postgres 는 실패한 문장 뒤로 트랜잭션이 통째로 중단된다(SQLite 는 아니었다).
        # rollback 없이는 아래 delete/update 가 "current transaction is aborted" 로 또 실패한다.
        conn.rollback()
        if exc.diag.constraint_name == "sources_fingerprint_key":
            conn.execute("delete from sources where id = %s", (source_id,))
            conn.commit()
            raise IngestError("같은 내용의 원본이 이미 등록돼 있다") from exc
        fail_source(conn, source_id, f"{type(exc).__name__}: {exc}")
        raise
    except Exception as exc:
        # DB 오류가 아니어도(ffmpeg 실패 등) 열려 있을 수 있는 트랜잭션을 정리하고 FAILED 를 적는다.
        conn.rollback()
        fail_source(conn, source_id, f"{type(exc).__name__}: {exc}")
        raise


def add_source(
    conn: psycopg.Connection,
    cfg: config.Config,
    raw_path: str,
    title: str,
    content_type: str,
    origin: str | None,
    context: str | None,
    language: str | None = None,
) -> int:
    """이미 있는 파일을 한 번에 등록한다 — begin + finish."""
    path = resolve_source_path(cfg, raw_path)
    begun = begin_source(conn, cfg, path, title, content_type, origin, context, language)
    if begun.reused:
        raise IngestError(f"이미 등록된 원본이다 (source {begun.source_id})")
    finish_source(conn, cfg, begun.source_id, path)
    return begun.source_id


def add_chunk(
    conn: psycopg.Connection,
    cfg: config.Config,
    source_id: int,
    start: float,
    end: float,
    replace: bool = False,
) -> int:
    """구간의 오디오를 뽑아 청크로 만든다.

    🔴 LECTURE 는 **소스당 청크 1개**다(migrations/001_baseline.sql chunks 주석). 이미 있으면 거부하고,
    `replace=True` 면 기존 청크와 그 아래 전부(발화·구간·클립, 그리고 그 구간을 가리키는 run)를
    지우고 **같은 idx** 로 다시 만든다. 예전엔 idx 를 올려 옆에 하나 더 만들었는데, 화면은
    첫 청크만 보고 파이프라인은 전 청크를 봐서 "다시 추출" 뒤에 둘이 서로 다른 것을 봤다.
    """
    row = conn.execute("select path, content_type, duration_sec from sources where id = %s", (source_id,)).fetchone()
    if row is None:
        raise IngestError(f"source {source_id} 없음")
    if row["duration_sec"] and end > row["duration_sec"]:
        raise IngestError(f"end={end} 가 원본 길이 {row['duration_sec']:.0f}s 를 넘는다")

    # LECTURE 는 화면을 보지 않는다(§4-1) — STT 입력만 있으면 되므로 오디오만 뽑는다.
    # 부수 효과가 하나 더 있다: 영상 복사는 키프레임에 스냅돼 시작이 최대 몇 초 밀리는데,
    # 오디오 재인코딩은 샘플 단위로 정확해서 청크 시작 초가 요청값과 일치한다(§12).
    # 🔴 FILM 은 Gemini 에 영상을 넣어야 하므로 여기서 갈라진다.
    if row["content_type"] != "LECTURE":
        raise IngestError(f"{row['content_type']} 청크 추출은 아직 구현 안 됨 (2단계)")

    existing = conn.execute(
        "select id, idx from chunks where source_id = %s order by idx", (source_id,)
    ).fetchall()
    if existing and not replace:
        raise IngestError(
            f"source {source_id} 에 청크가 이미 있다 — LECTURE 는 청크 1개다. 다시 뽑으려면 replace"
        )
    idx = existing[0]["idx"] if existing else 0

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.work_dir / f"source{source_id}_chunk{idx}.wav"

    # 파생물 목록은 행을 지우기 전에 뽑아야 한다. 새 wav 는 옛것과 같은 이름이라 목록에서 뺀다 —
    # 아래에서 ffmpeg 가 덮어쓴 파일을 도로 지우게 된다.
    stale_files = [
        p for p in media.derived_paths(conn, cfg, source_id) if p.resolve() != out.resolve()
    ] if existing else []

    # 추출을 먼저 한다. 실패하면 옛 청크·전사가 그대로 남는다 — 몇 분짜리 STT 를 실패한 재추출
    # 때문에 잃지 않는다.
    # 오디오 추출도 80분짜리면 수십 초다. 트랜잭션을 열어둔 채 몇 분 계산하지 않는다 — 위 읽기로 열린 것을 끊는다.
    conn.commit()
    started = time.monotonic()
    ffmpeg.extract_audio(str(cfg.source_file(row["path"])), str(out), start, end)
    latency_ms = int((time.monotonic() - started) * 1000)

    if existing:
        # chunks → utterances·segments → clips 는 cascade. runs 는 source 에 매달려 있어 따로 지운다:
        # runs.ranked 가 사라진 구간 idx 를 가리키게 되고, 화면은 그걸 새 구간에 겹쳐 그린다.
        conn.execute("delete from chunks where source_id = %s", (source_id,))
        conn.execute("delete from runs where source_id = %s", (source_id,))
        media.remove_files(cfg, stale_files)

    cursor = conn.execute(
        "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, %s, %s, %s, %s) returning id",
        (source_id, idx, start, end, cfg.store_work(out)),
    )
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms) values (%s, 'chunk', %s)",
        (source_id, latency_ms),
    )
    chunk_id = cursor.fetchone()["id"]
    conn.commit()
    return chunk_id
