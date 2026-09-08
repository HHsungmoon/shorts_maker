"""시청자 API — 인터넷에 그대로 열리는 면.

여기서 지키는 것 셋(watch.py 머리 주석과 같다):
  1. 🔴 읽기는 **발행된 것만**. 미발행은 404 — 존재를 알리지 않는다
  2. 🔴 **외부 API 호출 0회.** 질문 등록에 임베딩도 LLM 도 없다(update_plan D11)
  3. 🔴 쓰기는 레이트리밋

인증 경계(무인증으로 닿는가, 스튜디오와 섞이지 않는가)는 test_api_auth.py 가 본다.
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from shorts_maker.adapters import gemini
from shorts_maker.answers import viewers
from shorts_maker.db import store
from shorts_maker.http import deps, server

from ..support import make_config, reset_db


class WatchTestCase(unittest.TestCase):
    """비밀번호가 **설정된** 서버로 테스트한다 — 운영과 같은 상태에서 공개 면이 열려 있는지 본다.

    무인증 로컬 개발 모드에서는 스튜디오도 열려 있어서, 그 상태로 테스트하면 "공개라서 됐다"와
    "인증이 꺼져 있어서 됐다"가 구분되지 않는다.
    """

    def setUp(self):
        self.url = reset_db()
        viewers.reset_limits()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = deps.cfg
        deps.cfg = make_config(
            root,
            web_dir=root / "nonexistent-dist",
            admin_password="test-admin-password",
            api_token="",
            cookie_secure=False,
        )
        self.client = TestClient(server.app)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(viewers.reset_limits)
        self.addCleanup(lambda: setattr(deps, "cfg", self._saved))

    def source(self, title="발행된 강연", published=True, fingerprint="sha256:a", **extra) -> int:
        columns = {"title": title, "content_type": "LECTURE", "path": "a.mp4",
                   "fingerprint": fingerprint, "status": "DONE", "published": published,
                   "youtube_id": "abc123", **extra}
        names = ", ".join(columns)
        marks = ", ".join(["%s"] * len(columns))
        with store.connect(self.url) as conn:
            row = conn.execute(
                f"insert into sources ({names}) values ({marks}) returning id", tuple(columns.values())
            ).fetchone()
            conn.commit()
        return row["id"]

    def question(self, source_id: int, text="원문 질문", viewer_id="00000000-0000-4000-8000-000000000001") -> int:
        with store.connect(self.url) as conn:
            row = conn.execute(
                "insert into questions (source_id, text, viewer_id) values (%s, %s, %s) returning id",
                (source_id, text, viewer_id),
            ).fetchone()
            conn.commit()
        return row["id"]

    def rows(self, sql, params=()):
        with store.connect(self.url) as conn:
            return [dict(r) for r in conn.execute(sql, params)]


class PublishedOnlyTest(WatchTestCase):
    def test_the_listing_shows_only_published_sources(self):
        self.source(title="보임", published=True, fingerprint="sha256:a")
        self.source(title="안 보임", published=False, fingerprint="sha256:b")
        body = self.client.get("/api/watch/sources").json()
        self.assertEqual([s["title"] for s in body], ["보임"])

    def test_an_unpublished_source_is_404_not_403(self):
        # 🔴 403 이면 "그 id 는 있다"를 알려주는 셈이다.
        hidden = self.source(published=False)
        self.assertEqual(self.client.get(f"/api/watch/sources/{hidden}").status_code, 404)

    def test_questions_cannot_be_posted_to_an_unpublished_source(self):
        hidden = self.source(published=False)
        response = self.client.post(f"/api/watch/sources/{hidden}/questions", json={"text": "질문"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.rows("select count(*) as n from questions")[0]["n"], 0)

    def test_a_question_on_an_unpublished_source_cannot_be_liked(self):
        # 좋아요로 미발행 영상의 질문 존재를 확인할 수 없어야 한다.
        hidden = self.source(published=False)
        question_id = self.question(hidden)
        self.assertEqual(self.client.post(f"/api/watch/questions/{question_id}/like").status_code, 404)

    def test_an_unpublished_clip_file_is_404(self):
        source_id = self.source()
        with store.connect(self.url) as conn:
            run_id = conn.execute("insert into runs (source_id) values (%s) returning id", (source_id,)).fetchone()["id"]
            chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 300, 'c.wav') returning id",
                (source_id,),
            ).fetchone()["id"]
            segment_id = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx)"
                " values (%s, 0, 0, 30, 0, 3) returning id",
                (chunk_id,),
            ).fetchone()["id"]
            clip_id = conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec, path, rendered)"
                " values (%s, %s, 0, 30, 'clips/clip001.mp4', true) returning id",
                (run_id, segment_id),
            ).fetchone()["id"]
            conn.commit()
        self.assertEqual(self.client.get(f"/api/watch/clips/{clip_id}/file").status_code, 404)


class NoExternalCallsTest(WatchTestCase):
    """🔴 공개 경로에서 유료 API 를 부르면 그게 공격면이다. 질문은 insert 만 한다(D11)."""

    def test_posting_a_question_never_calls_gemini(self):
        source_id = self.source()
        with mock.patch.object(gemini, "generate_json", autospec=True) as llm:
            response = self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "연봉이 궁금해요"})
        self.assertEqual(response.status_code, 200, response.text)
        llm.assert_not_called()

    def test_the_whole_public_surface_works_without_an_api_key(self):
        # GEMINI_API_KEY 가 비어도 공개 면은 전부 동작해야 한다 — 외부 호출이 0회라는 증명이다.
        deps.cfg = dataclasses.replace(deps.cfg, gemini_api_key="")
        source_id = self.source()
        self.assertEqual(self.client.get("/api/watch/sources").status_code, 200)
        posted = self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "질문"})
        self.assertEqual(posted.status_code, 200, posted.text)
        question_id = posted.json()["questionId"]
        self.assertEqual(self.client.post(f"/api/watch/questions/{question_id}/like").status_code, 200)
        self.assertEqual(self.client.get(f"/api/watch/sources/{source_id}").status_code, 200)

    def test_a_new_question_starts_without_a_cluster(self):
        # 묶기는 크리에이터의 [집계] 가 한다. 등록 시점에는 소속이 없다.
        source_id = self.source()
        self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "질문"})
        self.assertIsNone(self.rows("select cluster_id from questions")[0]["cluster_id"])


class ViewerCookieTest(WatchTestCase):
    def test_a_cookie_is_issued_on_a_plain_read(self):
        # 목록만 봐도 발급된다 — 질문을 쓰는 시점에 처음 만들면 그 요청만 신원이 없다.
        self.source()
        response = self.client.get("/api/watch/sources")
        self.assertIn(viewers.COOKIE_NAME, response.cookies)

    def test_the_cookie_is_not_readable_by_scripts(self):
        self.source()
        header = self.client.get("/api/watch/sources").headers["set-cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=lax", header)

    def test_a_forged_cookie_value_is_replaced(self):
        # 🔴 임의 문자열을 그대로 viewer_id 로 쓰면 남이 정한 값이 DB 에 들어간다.
        self.source()
        self.client.cookies.set(viewers.COOKIE_NAME, "'; drop table questions; --")
        response = self.client.get("/api/watch/sources")
        self.assertIn(viewers.COOKIE_NAME, response.cookies)

    def test_the_same_viewer_keeps_one_identity_across_requests(self):
        source_id = self.source()
        self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "하나"})
        self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "둘"})
        viewer_ids = {r["viewer_id"] for r in self.rows("select viewer_id from questions")}
        self.assertEqual(len(viewer_ids), 1)


class LikeTest(WatchTestCase):
    def test_liking_twice_toggles_instead_of_stacking(self):
        source_id = self.source()
        question_id = self.question(source_id)
        first = self.client.post(f"/api/watch/questions/{question_id}/like").json()
        self.assertEqual(first, {"liked": True, "likes": 1})
        second = self.client.post(f"/api/watch/questions/{question_id}/like").json()
        self.assertEqual(second, {"liked": False, "likes": 0})

    def test_the_detail_tells_the_viewer_what_they_liked(self):
        # 🔴 쿠키가 httpOnly 라 브라우저는 자기 id 를 못 읽는다. 서버가 알려줘야 한다.
        source_id = self.source()
        question_id = self.question(source_id)
        self.client.post(f"/api/watch/questions/{question_id}/like")
        question = self.client.get(f"/api/watch/sources/{source_id}").json()["questions"][0]
        self.assertTrue(question["liked_by_me"])
        self.assertEqual(question["likes"], 1)

    def test_another_viewer_does_not_see_it_as_their_like(self):
        source_id = self.source()
        question_id = self.question(source_id)
        self.client.post(f"/api/watch/questions/{question_id}/like")
        self.client.cookies.clear()
        question = self.client.get(f"/api/watch/sources/{source_id}").json()["questions"][0]
        self.assertFalse(question["liked_by_me"])
        self.assertEqual(question["likes"], 1)

    def test_questions_come_back_most_liked_first(self):
        source_id = self.source()
        quiet = self.question(source_id, text="조용한 질문")
        loud = self.question(source_id, text="인기 질문")
        self.client.post(f"/api/watch/questions/{loud}/like")
        texts = [q["text"] for q in self.client.get(f"/api/watch/sources/{source_id}").json()["questions"]]
        self.assertEqual(texts, ["인기 질문", "조용한 질문"])
        self.assertEqual(len(texts), 2)
        self.assertNotEqual(quiet, loud)


class LimitsTest(WatchTestCase):
    def test_questions_longer_than_the_cap_are_refused(self):
        source_id = self.source()
        response = self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "가" * 201})
        self.assertEqual(response.status_code, 422)

    def test_a_blank_question_is_refused(self):
        source_id = self.source()
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                response = self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": blank})
                self.assertIn(response.status_code, (422,))
        self.assertEqual(self.rows("select count(*) as n from questions")[0]["n"], 0)

    def test_posting_too_many_questions_gets_429(self):
        source_id = self.source()
        for _ in range(viewers.LIMITS["question"]):
            self.assertEqual(
                self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "질문"}).status_code, 200
            )
        blocked = self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "하나 더"})
        self.assertEqual(blocked.status_code, 429)
        # 🔴 막힌 요청이 저장되면 안 된다.
        self.assertEqual(self.rows("select count(*) as n from questions")[0]["n"], viewers.LIMITS["question"])

    def test_clearing_the_cookie_still_hits_the_ip_ceiling(self):
        # 쿠키를 지워가며 도는 한 사람은 IP 한도(viewer 한도 × 3)에서 걸린다.
        source_id = self.source()
        allowed = viewers.LIMITS["question"] * viewers.IP_MULTIPLIER
        for _ in range(allowed):
            self.client.cookies.clear()
            self.assertEqual(
                self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "질문"}).status_code, 200
            )
        self.client.cookies.clear()
        blocked = self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "하나 더"})
        self.assertEqual(blocked.status_code, 429)

    def test_reading_is_never_rate_limited(self):
        # 읽기까지 세면 목록을 새로고침하는 것만으로 막힌다.
        self.source()
        for _ in range(viewers.LIMITS["question"] * 5):
            self.assertEqual(self.client.get("/api/watch/sources").status_code, 200)


class EventTest(WatchTestCase):
    def test_a_question_and_a_like_are_recorded_as_events(self):
        source_id = self.source()
        posted = self.client.post(f"/api/watch/sources/{source_id}/questions", json={"text": "질문"})
        self.client.post(f"/api/watch/questions/{posted.json()['questionId']}/like")
        kinds = [r["kind"] for r in self.rows("select kind from viewer_events order by id")]
        self.assertEqual(kinds, ["question_post", "like"])

    def test_the_client_cannot_forge_server_side_events(self):
        # 🔴 question_post·like 를 클라이언트가 넣을 수 있으면 퍼널 수치가 거짓이 된다.
        source_id = self.source()
        for kind in ("question_post", "like"):
            with self.subTest(kind=kind):
                response = self.client.post("/api/watch/events", json={"kind": kind, "sourceId": source_id})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.rows("select count(*) as n from viewer_events")[0]["n"], 0)

    def test_a_playback_event_is_accepted(self):
        source_id = self.source()
        response = self.client.post(
            "/api/watch/events", json={"kind": "cta_click", "sourceId": source_id, "payload": {"at": 12.5}}
        )
        self.assertEqual(response.status_code, 200, response.text)
        row = self.rows("select kind, source_id, payload from viewer_events")[0]
        self.assertEqual((row["kind"], row["source_id"]), ("cta_click", source_id))
        self.assertEqual(row["payload"], {"at": 12.5})

    def test_an_unknown_event_kind_is_refused(self):
        self.assertEqual(self.client.post("/api/watch/events", json={"kind": "scroll"}).status_code, 422)



class PublishedClipTest(WatchTestCase):
    """발행된 클립이 시청자에게 어떻게 보이는가."""

    def make_clip(self, source_id: int, question: str | None = "마케터 인재상은?",
                  published: bool = True, asked: int = 1) -> int:
        with store.connect(self.url) as conn:
            run_id = conn.execute(
                "insert into runs (source_id) values (%s) returning id", (source_id,)
            ).fetchone()["id"]
            chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 300, 'c.wav') returning id", (source_id,)
            ).fetchone()["id"]
            segment_id = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
                " end_utterance_idx) values (%s, 0, 0, 30, 0, 3) returning id", (chunk_id,)
            ).fetchone()["id"]
            cluster_id = None
            if question:
                cluster_id = conn.execute(
                    "insert into question_clusters (source_id, canonical_text, status)"
                    " values (%s, %s, 'PUBLISHED') returning id", (source_id, question)
                ).fetchone()["id"]
                for n in range(asked):
                    conn.execute(
                        "insert into questions (source_id, text, viewer_id, cluster_id)"
                        " values (%s, %s, %s, %s)", (source_id, f"원문 {n}", f"v{n}", cluster_id)
                    )
            clip_id = conn.execute(
                """insert into clips (run_id, segment_id, start_sec, end_sec, path, rendered,
                                      total_sec, question_cluster_id, published_at)
                   values (%s, %s, 0, 30, 'clips/clip001.mp4', true, 28.5, %s, %s) returning id""",
                (run_id, segment_id, cluster_id, "now()" if published else None),
            ).fetchone()["id"]
            if published:
                conn.execute("update clips set published_at = now() where id = %s", (clip_id,))
            conn.commit()
        return clip_id

    def test_a_published_clip_carries_the_question_it_answers(self):
        # 🔴 클립만 주면 시청자가 "이게 왜 여기 있지" 가 된다. 이 제품의 요지는 "당신이 물어본 것에
        # 대한 답" 이고, 그 연결이 응답에 있어야 화면이 그릴 수 있다.
        source_id = self.source()
        self.make_clip(source_id, question="마케터 인재상은?", asked=3)
        clip = self.client.get(f"/api/watch/sources/{source_id}").json()["clips"][0]
        self.assertEqual(clip["question"], "마케터 인재상은?")
        self.assertEqual(clip["asked_by"], 3)
        self.assertEqual(clip["total_sec"], 28.5)

    def test_an_unpublished_clip_is_absent_from_the_detail(self):
        source_id = self.source()
        self.make_clip(source_id, published=False)
        self.assertEqual(self.client.get(f"/api/watch/sources/{source_id}").json()["clips"], [])

    def test_unanswerable_questions_are_listed_with_their_suggestion(self):
        source_id = self.source()
        other = self.source(title="ep.2", fingerprint="sha256:b")
        with store.connect(self.url) as conn:
            conn.execute(
                """insert into question_clusters (source_id, canonical_text, status, suggested_source_id)
                   values (%s, '면접 준비는요?', 'UNANSWERABLE', %s)""",
                (source_id, other),
            )
            conn.commit()
        entry = self.client.get(f"/api/watch/sources/{source_id}").json()["unanswerable"][0]
        self.assertEqual(entry["question"], "면접 준비는요?")
        self.assertEqual((entry["suggested_source_id"], entry["suggested_title"]), (other, "ep.2"))

    def test_an_unpublished_suggestion_is_not_advertised(self):
        # 🔴 발행하지 않은 영상을 가리키면 시청자가 404 로 간다.
        source_id = self.source()
        hidden = self.source(title="비공개편", published=False, fingerprint="sha256:c")
        with store.connect(self.url) as conn:
            conn.execute(
                """insert into question_clusters (source_id, canonical_text, status, suggested_source_id)
                   values (%s, '면접 준비는요?', 'UNANSWERABLE', %s)""",
                (source_id, hidden),
            )
            conn.commit()
        entry = self.client.get(f"/api/watch/sources/{source_id}").json()["unanswerable"][0]
        self.assertIsNone(entry["suggested_source_id"])
        self.assertIsNone(entry["suggested_title"])

    def test_a_clip_from_another_source_does_not_leak_in(self):
        mine = self.source()
        theirs = self.source(title="다른 영상", fingerprint="sha256:d")
        self.make_clip(theirs)
        self.assertEqual(self.client.get(f"/api/watch/sources/{mine}").json()["clips"], [])


class ClipTitleTest(WatchTestCase):
    """🔴 숏폼 목록에는 질문에서 나온 클립과 그렇지 않은 클립이 **함께** 올라간다.

    질문을 제목처럼 쓰던 시절에는 기준으로 뽑은 클립의 제목 자리가 비었다(2026-09-08).
    이제 제목은 컬럼이고, 크리에이터가 고친 문장이 있으면 그게 우선이다.
    """

    def publish(self, source_id: int, title: str | None, question: str | None) -> int:
        with store.connect(self.url) as conn:
            run_id = conn.execute(
                "insert into runs (source_id) values (%s) returning id", (source_id,)
            ).fetchone()["id"]
            chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 300, 'c.wav') returning id", (source_id,)
            ).fetchone()["id"]
            segment_id = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
                " end_utterance_idx) values (%s, 0, 0, 30, 0, 3) returning id", (chunk_id,)
            ).fetchone()["id"]
            cluster_id = None
            if question:
                cluster_id = conn.execute(
                    "insert into question_clusters (source_id, canonical_text) values (%s, %s)"
                    " returning id", (source_id, question)
                ).fetchone()["id"]
            clip_id = conn.execute(
                """insert into clips (run_id, segment_id, start_sec, end_sec, rendered,
                                      question_cluster_id, title, published_at)
                   values (%s, %s, 0, 30, true, %s, %s, now()) returning id""",
                (run_id, segment_id, cluster_id, title),
            ).fetchone()["id"]
            conn.commit()
        return clip_id

    def titles(self, source_id: int) -> list[str | None]:
        return [c["title"] for c in self.client.get(f"/api/watch/sources/{source_id}").json()["clips"]]

    def test_a_clip_with_no_question_still_has_a_title(self):
        source_id = self.source()
        self.publish(source_id, title="쏘카의 기술 문화", question=None)
        self.assertEqual(self.titles(source_id), ["쏘카의 기술 문화"])

    def test_an_edited_title_wins_over_the_question(self):
        source_id = self.source()
        self.publish(source_id, title="크리에이터가 고친 제목", question="연봉은 얼마인가요?")
        self.assertEqual(self.titles(source_id), ["크리에이터가 고친 제목"])

    def test_the_question_is_the_fallback_title(self):
        source_id = self.source()
        self.publish(source_id, title=None, question="연봉은 얼마인가요?")
        self.assertEqual(self.titles(source_id), ["연봉은 얼마인가요?"])
        # 질문 자체도 따로 온다 — 화면이 "몇 명이 물어봤다"를 붙이는 데 쓴다.
        clip = self.client.get(f"/api/watch/sources/{source_id}").json()["clips"][0]
        self.assertEqual(clip["question"], "연봉은 얼마인가요?")


if __name__ == "__main__":
    unittest.main()
