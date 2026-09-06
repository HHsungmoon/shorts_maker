"""Postgres 접속과 스키마 적용.

2026-09-06 에 SQLite 에서 옮겼다(update_plan D5 번복). 이유는 셋 — ① SQLite 는 CHECK·제약을
ALTER 로 못 바꿔 마이그레이션마다 테이블 재생성 곡예가 필요했다(v9 에서 실제로 겪었다),
② 서비스가 되면 잡 워커를 별도 프로세스로 빼야 하는데 단일 쓰기 잠금이 웹 서버와 워커를 서로
막는다, ③ 사용자·인가가 붙으면 전 테이블에 소유자 스코프가 들어가고 그건 Postgres 에선 ALTER
한 줄이다. ORM 은 쓰지 않는다 — SQL 이 눈에 보이는 게 지금 코드의 장점이고, 런타임 근거가 없다.

마이그레이션은 `migrations/NNN_*.sql` 을 번호 순으로 적용하고 `schema_version` 에 적는다.
🔴 파일 하나 = 트랜잭션 하나. Postgres 는 DDL 도 트랜잭션이라 중간에 죽으면 그 파일은 통째로
되돌아가고, 다음 기동 때 다시 시도된다. 이미 적용된 파일은 절대 고치지 않는다 — 새 번호로 덧붙인다.
"""

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).with_name("migrations")

# `sm db status` 가 세는 테이블. 새 테이블을 만들면 여기도 넣는다(tests/db 가 확인한다).
TABLES = (
    "sources", "chunks", "utterances", "segments", "runs", "clips", "clip_parts", "clip_reviews",
    "stage_calls", "question_clusters", "questions", "question_likes", "embeddings", "viewer_events",
)


class SchemaError(RuntimeError):
    pass


# ---------------------------------------------------------------- 접속

_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def pool(url: str) -> ConnectionPool:
    """URL 당 풀 하나. 프로세스 안에서 공유한다 — API 스레드와 잡 워커가 같은 풀을 쓴다.

    max_size 8: uvicorn 의 동기 엔드포인트는 스레드풀에서 돌고 잡 워커는 1개다. 동시에 연결을 쥐는
    수가 그보다 훨씬 적다. Postgres 기본 max_connections(100) 안에서 넉넉하다.
    """
    with _pools_lock:
        found = _pools.get(url)
        if found is None:
            found = ConnectionPool(
                url, min_size=1, max_size=8, open=True,
                # 행을 dict 로 받는다. `row["col"]` 이 코드 전체의 관례다.
                kwargs={"row_factory": dict_row},
            )
            _pools[url] = found
        return found


@contextmanager
def connect(url: str) -> Iterator[psycopg.Connection]:
    """`with store.connect(url) as conn:` — 정상 종료면 commit, 예외면 rollback 하고 풀에 돌려준다.

    안에서 `conn.commit()` 을 불러도 된다(긴 잡이 중간 결과를 먼저 굳힐 때). 🔴 STT 처럼 몇 분 도는
    계산 앞에서는 먼저 commit 해서 트랜잭션을 끊는다 — 읽기만 했어도 트랜잭션이 열려 있다.
    """
    with pool(url).connection() as conn:
        yield conn


def close_pools() -> None:
    """테스트·종료 시. 열린 풀을 닫는다."""
    with _pools_lock:
        for p in _pools.values():
            p.close()
        _pools.clear()


# ---------------------------------------------------------------- 스키마

def migration_files() -> list[tuple[int, Path]]:
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        try:
            found.append((int(path.name.split("_", 1)[0]), path))
        except ValueError as exc:
            raise SchemaError(f"마이그레이션 파일명은 NNN_이름.sql 이어야 한다: {path.name}") from exc
    return found


SCHEMA_VERSION = max((n for n, _ in migration_files()), default=0)


def schema_version(conn: psycopg.Connection) -> int:
    conn.execute(
        "create table if not exists schema_version"
        " (version integer primary key, applied_at timestamptz not null default now())"
    )
    row = conn.execute("select max(version) as v from schema_version").fetchone()
    conn.commit()
    return row["v"] or 0


def apply_schema(conn: psycopg.Connection) -> int:
    """아직 안 된 마이그레이션 파일을 번호 순으로 적용한다. 파일 하나가 트랜잭션 하나다."""
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise SchemaError(f"DB 가 v{current} 인데 코드는 v{SCHEMA_VERSION} 이다 — 코드가 오래됐다")
    for number, path in migration_files():
        if number <= current:
            continue
        # 파라미터 없는 execute 는 여러 문장을 한 번에 보낸다. 실패하면 파일 전체가 롤백된다.
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("insert into schema_version (version) values (%s)", (number,))
        conn.commit()
    return SCHEMA_VERSION


def existing_tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "select table_name from information_schema.tables"
        " where table_schema = 'public' and table_type = 'BASE TABLE' order by table_name"
    ).fetchall()
    return [r["table_name"] for r in rows]


def row_counts(conn: psycopg.Connection) -> dict[str, int]:
    present = set(existing_tables(conn))
    # 테이블명은 TABLES 상수에서만 오므로 f-string 삽입이 안전하다(사용자 입력 아님).
    return {t: conn.execute(f"select count(*) as n from {t}").fetchone()["n"] for t in TABLES if t in present}


def reset(url: str) -> None:
    """public 스키마를 통째로 지우고 다시 만든다. 되돌릴 수 없다 — 호출부에서 명시적 동의를 받는다."""
    with connect(url) as conn:
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
        conn.commit()


def ensure_database(url: str) -> None:
    """URL 의 데이터베이스가 없으면 만든다(테스트 DB 용). 같은 서버의 `postgres` DB 로 붙어서 한다.

    CREATE DATABASE 는 트랜잭션 안에서 못 돌아 autocommit 이 필요하다.
    """
    info = psycopg.conninfo.conninfo_to_dict(url)
    name = info.pop("dbname")
    with psycopg.connect(**info, dbname="postgres", autocommit=True) as admin:
        exists = admin.execute("select 1 from pg_database where datname = %s", (name,)).fetchone()
        if not exists:
            admin.execute(psycopg.sql.SQL("create database {}").format(psycopg.sql.Identifier(name)))
