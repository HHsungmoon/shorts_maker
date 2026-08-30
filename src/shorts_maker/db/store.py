"""SQLite 접속과 스키마 적용.

마이그레이션 도구를 안 쓰는 이유: A 단계에서는 스키마가 매일 바뀌고 DB 를 통째로
날리는 게 더 빠르다. Postgres 로 옮기는 C5 에서 도구를 도입한다(문서 §9-10).
"""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 7
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

TABLES = ("sources", "chunks", "utterances", "segments", "runs", "clips", "clip_reviews", "stage_calls")

# 버전별 제자리 업그레이드. **덧붙이기만 가능한 것**(테이블·인덱스·컬럼 추가)만 여기 넣는다.
# 🔴 제약 변경은 SQLite 에서 테이블 재생성이 필요하고 조용히 어긋날 여지가 커서 넣지 않는다 —
# 그래서 새로 넣는 컬럼에는 check 를 걸지 않는다(걸면 새 DB 와 마이그레이션한 DB 가 달라진다).
# A 단계엔 통째로 날리는 게 정상 경로였지만, STT 한 번에 수 분이 드는 지금은 그 비용이 실제로 아프다.
MIGRATIONS: dict[int, list[str]] = {
    5: ["create unique index if not exists uq_clips_run_segment on clips (run_id, segment_id)"],
    6: ["alter table sources add column language text"],
    7: [
        "alter table stage_calls add column total_tokens integer",
        "alter table stage_calls add column cached_tokens integer",
    ],
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 🔴 SQLite 는 외래키를 연결마다 명시적으로 켜야 한다(기본 off).
    # 이걸 빼면 on delete cascade 도 check 도 아니고 참조 무결성 자체가 안 걸린다.
    conn.execute("pragma foreign_keys = on")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("create table if not exists schema_version (version integer not null)")
    row = conn.execute("select max(version) as v from schema_version").fetchone()
    conn.commit()
    return row["v"] or 0


class SchemaError(RuntimeError):
    pass


def _apply_once(conn: sqlite3.Connection, statement: str) -> None:
    """이미 적용된 문장은 넘어간다.

    🔴 SQLite 에는 `add column if not exists` 가 없다. ALTER 는 성공했는데 버전 기록 직전에
    죽으면, 다시 돌릴 때 `duplicate column name` 으로 막혀 손으로 고쳐야 한다 —
    마이그레이션은 몇 번을 돌려도 같은 결과여야 한다.
    """
    try:
        conn.execute(statement)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def apply_schema(conn: sqlite3.Connection) -> int:
    """스키마를 적용한다. 빈 DB 에만 적용되고, 구버전 DB 는 거부한다.

    🔴 schema.sql 은 전부 `create table if not exists` 라서 **테이블 추가는 되지만 컬럼
    추가는 안 된다.** 구버전 DB 에 그냥 돌리면 컬럼 없이 버전만 올라가 — 스키마와 버전이
    어긋난 채로 조용히 굴러간다. 그래서 올릴 수 없으면 올리지 않고 멈춘다.
    A 단계는 어차피 통째로 날리는 게 정상 경로다(`sm db reset --yes`).
    """
    current = schema_version(conn)
    if current == SCHEMA_VERSION:
        return current
    if current > SCHEMA_VERSION:
        raise SchemaError(f"DB 가 v{current} 인데 코드는 v{SCHEMA_VERSION} 이다 — 코드가 오래됐다")
    if current > 0:
        steps = [v for v in range(current + 1, SCHEMA_VERSION + 1)]
        missing = [v for v in steps if v not in MIGRATIONS]
        if missing:
            raise SchemaError(
                f"DB 가 v{current}, 코드가 v{SCHEMA_VERSION} 이다. v{missing} 은 제자리 업그레이드가"
                " 불가능하다 — `sm db reset --yes` 로 다시 만든다"
            )
        for version in steps:
            for statement in MIGRATIONS[version]:
                _apply_once(conn, statement)
            conn.execute("insert into schema_version (version) values (?)", (version,))
        conn.commit()
        return SCHEMA_VERSION
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("insert into schema_version (version) values (?)", (SCHEMA_VERSION,))
    conn.commit()
    return SCHEMA_VERSION


def existing_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name"
    ).fetchall()
    return [r["name"] for r in rows]


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    present = set(existing_tables(conn))
    # 테이블명은 TABLES 상수에서만 오므로 f-string 삽입이 안전하다(사용자 입력 아님).
    return {t: conn.execute(f"select count(*) as n from {t}").fetchone()["n"] for t in TABLES if t in present}


def reset(db_path: Path) -> None:
    """DB 파일을 지우고 새로 만든다. 되돌릴 수 없다 — 호출부에서 명시적 동의를 받는다."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix)
        candidate.unlink(missing_ok=True)
