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
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .. import config
from ..adapters import ffmpeg
from ..db import store
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
    youtube_id: str | None = None,
    channel: str | None = None,
) -> Begun:
    """원본 행을 **파일이 준비되기 전에** RUNNING 으로 만든다.

    `youtube_id`·`channel` 은 유튜브에서 받은 경우에만 채워진다(`ytdlp.probe` 가 준다).
    🔴 `origin` 문자열을 나중에 파싱해서 되찾지 않는다 — 형식이 바뀌면 조용히 깨진다.
    id 가 있어야 시청자 화면이 임베드 플레이어를 띄우고, channel 이 있어야 채널 횡단 검색(tease §5-3 b)
    의 범위를 정할 수 있다.

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
               youtube_id = coalesce(%s, youtube_id), channel = coalesce(%s, channel),
               status = 'RUNNING', error = null, updated_at = now() where id = %s""",
            # coalesce: 이번에 안 넘어온 값은 지우지 않는다. 유튜브로 받은 뒤 파일 경로로 다시
            # 등록하는 경우, 이미 있는 id 를 null 로 덮으면 임베드가 사라진다.
            (title, content_type, origin, context, language, youtube_id, channel, existing_id),
        )
        conn.commit()
        return Begun(existing_id, reused=False)

    cursor = conn.execute(
        """insert into sources (title, content_type, path, origin, fingerprint, context, language,
                                youtube_id, channel, status)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'RUNNING') returning id""",
        # 🔴 source_dir 기준 상대경로로 저장한다(config.Config.store_source 주석).
        (title, content_type, cfg.store_source(path), origin, f"pending:{uuid.uuid4().hex}", context,
         language, youtube_id, channel),
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


def plan_chunks(
    start_sec: float,
    end_sec: float,
    max_sec: float,
    silences: list[tuple[float, float]] | None = None,
) -> list[tuple[float, float]]:
    """분석할 범위 [start, end] 를 청크 경계로 나눈다.

    🔴 **몇 조각으로 나눌지는 사용자가 정하지 않는다.** 메모리 상한이 정한다(config 주석).
    규칙은 둘뿐이다:
      ① 조각 하나가 `max_sec` 을 넘지 않는 **최소 개수**로 나눈다 — `ceil(범위 / max_sec)`
      ② 그 개수로 **균등 분할**한다. 95분을 30분 상한으로 나누면 30/30/30/5 가 아니라 24×4 다.
         마지막만 짧으면 그 조각의 전사가 유난히 빨리 끝나 진행률이 거짓말을 한다.

    경계는 목표 지점 근처의 **무음 한가운데**로 당긴다. 고정 길이로 자르면 문장 한복판에서
    끊겨 그 발화가 양쪽 청크에서 모두 반토막 난다. 무음이 없으면 균등 분할 그대로 쓴다.
    """
    span = end_sec - start_sec
    if span <= 0:
        raise IngestError(f"범위가 비었다: {start_sec}s ~ {end_sec}s")
    if span <= max_sec:
        return [(start_sec, end_sec)]

    count = math.ceil(span / max_sec)
    step = span / count
    # 목표에서 이만큼 안에 있는 무음만 쓴다. 너무 멀리서 당기면 조각 길이가 들쭉날쭉해진다.
    window = min(45.0, step * 0.15)

    bounds = [start_sec]
    for index in range(1, count):
        snapped = _snap_to_silence(start_sec + step * index, window, silences or [])
        # 앞 경계를 넘어서면 빈 청크가 된다.
        bounds.append(round(max(snapped, bounds[-1] + 1.0), 3))
    # 🔴 마지막 경계는 **반올림하지 않는다.** round(5736.048617, 3) = 5736.049 가 되어 원본 길이를
    # 넘고, add_chunk 의 "end 가 길이를 넘는다" 가드에 걸린다(2026-09-06에 겪었다).
    bounds.append(end_sec)
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


def _snap_to_silence(target: float, window: float, silences: list[tuple[float, float]]) -> float:
    best, best_distance = target, window
    for start, end in silences:
        middle = (start + end) / 2
        distance = abs(middle - target)
        if distance < best_distance:
            best, best_distance = middle, distance
    return best


def add_chunks(
    conn: psycopg.Connection,
    cfg: config.Config,
    source_id: int,
    start_sec: float | None = None,
    end_sec: float | None = None,
    replace: bool = False,
) -> list[int]:
    """분석할 범위를 잘라 청크를 만든다. **사용자에게는 이게 "구간 추출" 한 번이다.**

    사용자가 정하는 건 **범위**(어디부터 어디까지 분석할까)이고, 그 안을 몇 조각으로 나눌지는
    코드가 정한다(plan_chunks). 범위를 비우면 영상 전체다.

    🔴 같은 원본에 긴 작업이 돌고 있으면 거부한다. 전사 중에 청크를 지우면 그 전사가 외래키
    위반으로 죽는다 — 실제로 당했다(store.source_lock).
    """
    row = conn.execute(
        "select path, duration_sec from sources where id = %s", (source_id,)
    ).fetchone()
    if row is None:
        raise IngestError(f"source {source_id} 없음")
    if not row["duration_sec"]:
        raise IngestError("원본 길이를 모른다 — 등록이 끝나지 않았다")

    duration = float(row["duration_sec"])
    start = max(0.0, float(start_sec or 0.0))
    end = min(duration, float(end_sec) if end_sec else duration)
    if end - start <= 0:
        raise IngestError(f"범위가 비었다: {start:.0f}s ~ {end:.0f}s (영상 길이 {duration:.0f}s)")

    with store.source_lock(conn, source_id, "작업"):
        silences: list[tuple[float, float]] = []
        if end - start > cfg.chunk_max_sec:
            # 나눌 때만 훑는다. 짧은 범위에 수십 초를 쓸 이유가 없다.
            silences = ffmpeg.detect_silences(str(cfg.source_file(row["path"])), binary=cfg.ffmpeg_bin)
        ranges = plan_chunks(start, end, cfg.chunk_max_sec, silences)

        made: list[int] = []
        for position, (piece_start, piece_end) in enumerate(ranges):
            # 첫 조각에서만 기존 것을 정리한다(replace). 나머지는 그 뒤에 이어 붙는다.
            made.append(
                add_chunk(conn, cfg, source_id, piece_start, piece_end,
                          replace=replace and position == 0,
                          idx=position, allow_more=position > 0)
            )
        return made


def add_chunk(
    conn: psycopg.Connection,
    cfg: config.Config,
    source_id: int,
    start: float,
    end: float,
    replace: bool = False,
    idx: int | None = None,
    allow_more: bool = False,
) -> int:
    """구간의 오디오를 뽑아 청크 하나를 만든다. 보통은 `add_chunks` 를 통해 불린다.

    🔴 청크는 **메모리 상한** 때문에 존재한다. 95분을 한 번에 전사하면 컨테이너 한도(3GB)를 넘어
    OOM 으로 죽는다(2026-09-06 실측: 30분 = 1.4GB). 그래서 긴 영상은 청크가 여러 개가 되고,
    그 사실은 사용자에게 보이지 않는다 — 화면은 소스 단위로 합쳐 보여준다.

    `allow_more=False` 면 이미 청크가 있을 때 거부한다 — 실수로 하나 더 만드는 걸 막는다.
    `replace=True` 는 기존 청크와 그 아래 전부(발화·구간·클립, 그리고 그 소스의 run)를 지운다.
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
    if existing and not replace and not allow_more:
        raise IngestError(f"source {source_id} 에 청크가 이미 있다 — 다시 뽑으려면 replace")
    if idx is None:
        idx = existing[0]["idx"] if existing else 0

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.work_dir / f"source{source_id}_chunk{idx}.wav"

    # 파생물 목록은 행을 지우기 전에 뽑아야 한다. 새 wav 는 옛것과 같은 이름이라 목록에서 뺀다 —
    # 아래에서 ffmpeg 가 덮어쓴 파일을 도로 지우게 된다.
    stale_files = [
        p for p in media.derived_paths(conn, cfg, source_id) if p.resolve() != out.resolve()
    ] if (existing and replace) else []

    # 추출을 먼저 한다. 실패하면 옛 청크·전사가 그대로 남는다 — 몇 분짜리 STT 를 실패한 재추출
    # 때문에 잃지 않는다.
    # 오디오 추출도 80분짜리면 수십 초다. 트랜잭션을 열어둔 채 몇 분 계산하지 않는다 — 위 읽기로 열린 것을 끊는다.
    conn.commit()
    started = time.monotonic()
    ffmpeg.extract_audio(str(cfg.source_file(row["path"])), str(out), start, end)
    latency_ms = int((time.monotonic() - started) * 1000)

    if existing and replace:
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
