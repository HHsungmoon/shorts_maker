"""테스트용 Config 와 DB.

🔴 Config 를 테스트에서 직접 생성하면 필드를 추가할 때마다 전 테스트가 깨진다(실제로 두 번
겪었다). `dataclasses.replace` 로 실제 로딩 결과를 덮어쓰면 새 필드는 자동으로 채워지고,
테스트가 신경 쓰는 값만 명시하게 된다.

DB 는 **진짜 Postgres** 다. SQLite 를 테스트용으로 남기는 이중 방언은 하지 않는다 — 그게
가장 흔한 함정이다. `docker compose up -d db` 로 띄운 서버의 `shorts_test` 데이터베이스를 쓰고
(없으면 만든다), 테스트마다 전 테이블을 truncate 한다. Postgres 가 안 떠 있으면 DB 테스트는
skip 되고 요약에 skipped 로 잡힌다 — 조용히 통과하는 게 아니다.
"""

import contextlib
import dataclasses
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

from shorts_maker import config
from shorts_maker.db import store

TEST_DB_NAME = "shorts_test"


def test_database_url() -> str:
    """실제 설정의 URL 에서 데이터베이스 이름만 shorts_test 로 바꾼다. 서버·계정은 같다."""
    parts = urlsplit(config.load().database_url)
    return urlunsplit(parts._replace(path="/" + TEST_DB_NAME))


TEST_URL = test_database_url()

_probe: str | None = None  # None = 아직 안 봄, "" = 접속됨, 그 외 = 실패 이유


def require_db() -> str:
    """Postgres 가 없으면 SkipTest. 한 번만 확인하고 결과를 기억한다."""
    global _probe
    if _probe is None:
        try:
            store.ensure_database(TEST_URL)
            with psycopg.connect(TEST_URL, connect_timeout=3):
                pass
            _probe = ""
        except Exception as exc:  # noqa: BLE001 — 어떤 이유든 "DB 없음"으로 skip
            _probe = f"{type(exc).__name__}: {exc}"
            print(
                f"\n⚠ Postgres 에 붙지 못해 DB 테스트를 건너뛴다: {_probe}\n"
                "   docker compose up -d db   (backend/.env 에 POSTGRES_PASSWORD 필요)\n",
                file=sys.stderr,
            )
    if _probe:
        raise unittest.SkipTest("Postgres 없음 — docker compose up -d db")
    return TEST_URL


def reset_db() -> str:
    """스키마를 최신으로 맞추고 전 테이블을 비운다. 각 테스트의 setUp 에서 부른다.

    스키마가 이미 최신이면 truncate(밀리초)로 끝난다. 아니면(마이그레이션이 바뀌었으면) public
    스키마를 통째로 지우고 다시 만든다.
    """
    url = require_db()
    with store.connect(url) as conn:
        if store.schema_version(conn) != store.SCHEMA_VERSION or set(store.TABLES) - set(store.existing_tables(conn)):
            conn.execute("drop schema public cascade")
            conn.execute("create schema public")
            conn.commit()
            store.apply_schema(conn)
        conn.execute(f"truncate table {', '.join(store.TABLES)} restart identity cascade")
        conn.commit()
    return url


def make_config(root: Path, **overrides) -> config.Config:
    base = dataclasses.replace(
        config.load(),
        # 실제 .env 의 키가 테스트로 새어들지 않게 한다.
        gemini_api_key="",
        api_token="",
        database_url=TEST_URL,
        work_dir=root / "work",
        source_dir=root / "sources",
    )
    return dataclasses.replace(base, **overrides)


class DbTestCase(unittest.TestCase):
    """비운 테스트 DB 에 연결된 `self.conn` 을 주는 케이스. 연결은 tearDown 에서 풀로 돌아간다.

    `self.conn` 은 psycopg 연결이다 — `execute(...).fetchone()` 이 dict 를 돌려주고, 커밋은 명시적으로
    `self.conn.commit()`. 롤백된 채 끝나도 다음 테스트가 truncate 하므로 상관없다.
    """

    def setUp(self) -> None:
        self.url = reset_db()
        self._stack = contextlib.ExitStack()
        self.conn = self._stack.enter_context(store.connect(self.url))
        self.addCleanup(self._stack.close)
