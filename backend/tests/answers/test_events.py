"""퍼널 집계 (tease §9, update_plan M6b).

여기서 지키는 것:
  1. 🔴 세는 단위는 **사람 수**다 — 같은 사람이 세 번 봐도 1이다. 이벤트 수로 세면 앞 칸이
     부풀어 유입률이 실제보다 낮아 보인다
  2. 🔴 이벤트가 없는 발행 클립도 **0 으로 표에 남는다** — 빠지면 "아무도 안 봤다" 와
     "발행된 적 없다" 가 구분되지 않는다
  3. 미발행 클립은 표에 없다. 발행하지 않은 것에 시청 기록이 있을 수 없다

이 표가 없으면 "숏폼이 원본 유입을 만든다"는 주장을 데이터로 할 수 없다. `viewer_events` 는
오래 **쓰기 전용**이었다 — 읽는 코드가 하나도 없었다.
"""

import unittest

from shorts_maker.answers import events

from ..support import DbTestCase


class FunnelTestCase(DbTestCase):
    def source(self, title="강연", fingerprint="sha256:a") -> int:
        return self.conn.execute(
            """insert into sources (title, content_type, path, fingerprint, status, published)
               values (%s, 'LECTURE', 'a.mp4', %s, 'DONE', true) returning id""",
            (title, fingerprint),
        ).fetchone()["id"]

    def chunk(self, source_id: int) -> int:
        """소스의 청크 하나. 이미 있으면 그것을 쓴다 — `unique (source_id, idx)` 때문에
        같은 소스에 클립을 둘 만들려면 청크를 공유해야 한다."""
        found = self.conn.execute(
            "select id from chunks where source_id = %s order by idx limit 1", (source_id,)
        ).fetchone()
        if found is not None:
            return found["id"]
        return self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 0, 300, 'c.wav') returning id", (source_id,)
        ).fetchone()["id"]

    def clip(self, source_id: int, question: str | None = None, published: bool = True) -> int:
        run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (source_id,)
        ).fetchone()["id"]
        chunk_id = self.chunk(source_id)
        # 구간 번호는 청크 안에서 유일해야 한다(`unique (chunk_id, idx)`).
        next_idx = self.conn.execute(
            "select coalesce(max(idx) + 1, 0) as n from segments where chunk_id = %s", (chunk_id,)
        ).fetchone()["n"]
        segment_id = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
            " end_utterance_idx) values (%s, %s, 0, 30, 0, 3) returning id", (chunk_id, next_idx)
        ).fetchone()["id"]
        cluster_id = None
        if question is not None:
            cluster_id = self.conn.execute(
                "insert into question_clusters (source_id, canonical_text, status)"
                " values (%s, %s, 'PUBLISHED') returning id", (source_id, question)
            ).fetchone()["id"]
        return self.conn.execute(
            f"""insert into clips (run_id, segment_id, start_sec, end_sec, total_sec,
                                   question_cluster_id, published_at)
                values (%s, %s, 0, 30, 28.5, %s, {"now()" if published else "null"})
                returning id""",
            (run_id, segment_id, cluster_id),
        ).fetchone()["id"]

    def see(self, viewer: str, kind: str, source_id: int, clip_id: int | None = None) -> None:
        events.record(self.conn, viewer, kind, source_id, clip_id)


class CountsPeopleTest(FunnelTestCase):
    def test_the_same_viewer_watching_three_times_counts_as_one(self):
        # 🔴 퍼널은 단계마다 **사람이 얼마나 남는지** 보는 도구다. 돌려 보기를 3으로 세면
        # 재생이 부풀어 "본 사람 중 원본으로 간 비율" 이 실제보다 낮게 보인다.
        source_id = self.source()
        clip_id = self.clip(source_id)
        for _ in range(3):
            self.see("viewer-a", "short_play", source_id, clip_id)
        row = events.funnel(self.conn, source_id)["clips"][0]
        self.assertEqual(row["short_play"], 1)

    def test_two_viewers_count_as_two(self):
        source_id = self.source()
        clip_id = self.clip(source_id)
        self.see("viewer-a", "short_play", source_id, clip_id)
        self.see("viewer-b", "short_play", source_id, clip_id)
        self.assertEqual(events.funnel(self.conn, source_id)["clips"][0]["short_play"], 2)

    def test_every_funnel_step_is_counted(self):
        source_id = self.source()
        clip_id = self.clip(source_id)
        for kind in events.FUNNEL_KINDS:
            self.see("viewer-a", kind, source_id, clip_id)
        row = events.funnel(self.conn, source_id)["clips"][0]
        self.assertEqual([row[kind] for kind in events.FUNNEL_KINDS], [1] * len(events.FUNNEL_KINDS))


class WhatAppearsInTheTableTest(FunnelTestCase):
    def test_a_published_clip_with_no_events_is_still_a_row_of_zeros(self):
        # 🔴 표에서 빠지면 "아무도 안 봤다" 와 "발행된 적 없다" 가 구분되지 않는다.
        source_id = self.source()
        self.clip(source_id)
        rows = events.funnel(self.conn, source_id)["clips"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["short_play"], 0)

    def test_an_unpublished_clip_is_absent(self):
        source_id = self.source()
        self.clip(source_id, published=False)
        self.assertEqual(events.funnel(self.conn, source_id)["clips"], [])

    def test_a_creator_picked_clip_appears_alongside_answers(self):
        """🔴 클러스터로 묶으면 질문에서 나오지 않은 클립이 표에서 사라진다.

        계획 문서는 "클러스터별 퍼널" 이라고 적었지만, 크리에이터가 자기 기준으로 뽑은 숏폼도
        같은 목록에 발행되고 시청자에게는 구분 없이 보인다. 그래서 묶는 단위는 클립이다.
        """
        source_id = self.source()
        self.clip(source_id, question="인재상은?")
        self.clip(source_id, question=None)
        rows = events.funnel(self.conn, source_id)["clips"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["question"] for row in rows}, {"인재상은?", None})

    def test_another_videos_clip_does_not_leak_in(self):
        mine = self.source()
        other = self.source(title="다른 강연", fingerprint="sha256:b")
        self.clip(other)
        self.assertEqual(events.funnel(self.conn, mine)["clips"], [])


class TotalsTest(FunnelTestCase):
    def test_questions_and_likes_land_in_the_totals(self):
        # 클립보다 먼저 일어나는 일이라 클립별로는 셀 수 없다 — 퍼널의 입구다.
        source_id = self.source()
        self.see("viewer-a", "question_post", source_id)
        self.see("viewer-b", "like", source_id)
        totals = events.funnel(self.conn, source_id)["totals"]
        self.assertEqual((totals["question_post"], totals["like"]), (1, 1))
        self.assertEqual(totals["viewers"], 2)

    def test_the_column_order_comes_from_the_server(self):
        # 화면이 순서를 다시 적으면 두 곳이 어긋난다. 서버가 정본이다.
        source_id = self.source()
        self.assertEqual(events.funnel(self.conn, source_id)["kinds"], list(events.FUNNEL_KINDS))


if __name__ == "__main__":
    unittest.main()
