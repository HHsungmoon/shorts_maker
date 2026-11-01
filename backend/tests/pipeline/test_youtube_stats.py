"""유튜브 성과 (tease §7-6, 2026-11-01).

🔴 지키는 것: ① 분석 권한이 있는 연결에서만 돈다 — 없으면 네트워크 전에 이유를 말한다 ② 지금 연결된 채널에
올린 것만 읽는다 ③ 유튜브에서 사라진 숏폼은 분석을 부르지 않고 "찾을 수 없음" ④ 분석이 비어 있으면 0 이 아니라
없음(None)이다 ⑤ 다시 가져오면 덮어쓴다(같은 날 두 줄 없음) ⑥ 중간에 실패하면 아무것도 바꾸지 않는다 ⑦ 성공도
실패도 `stage_calls` 에 남는다 ⑧ 우리 시청자 퍼널(viewer_events)과 섞지 않는다.
"""

import datetime as dt
import inspect
import tempfile
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import youtube
from shorts_maker.pipeline import youtube_channel, youtube_stats

from ..support import DbTestCase, make_config

FULL_GRANT = youtube.Grant(refresh_token="refresh-1", scopes=" ".join(youtube.SCOPES))
TODAY = dt.date(2026, 11, 3)


def totals(views=40, engaged=30, pct=75.5):
    return [{"views": views, "engagedViews": engaged, "estimatedMinutesWatched": 9.5, "averageViewDuration": 19,
             "averageViewPercentage": pct, "likes": 4, "comments": 1, "shares": 2, "subscribersGained": 1}]


def daily(*days):
    return [{"day": day, "views": 10, "engagedViews": 8, "estimatedMinutesWatched": 2.0, "averageViewDuration": 18,
             "averageViewPercentage": 70.0, "likes": 1, "comments": 0, "shares": 0, "subscribersGained": 0}
            for day in days]


class StatsTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name), youtube_client_id="cid", youtube_client_secret="secret")
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, youtube_id)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'orig1') returning id"
        ).fetchone()["id"]
        youtube_channel.save_channel(self.conn, FULL_GRANT)
        youtube_channel.note_channel(self.conn, "UCme", "내 채널")
        self.first = self.upload("v1")
        self.conn.commit()

    def upload(self, video_id: str, channel: str | None = "UCme", status: str = "DONE") -> int:
        return self.conn.execute(
            """insert into clip_uploads (source_id, title, privacy, status, youtube_video_id, channel_id, created_at)
               values (%s, %s, 'unlisted', %s, %s, %s, '2026-11-01T03:00:00Z') returning id""",
            (self.source_id, f"숏폼 {video_id}", status, video_id, channel),
        ).fetchone()["id"]

    def run_refresh(self, found=None, total=None, days=None, report=None):
        found = {"v1": youtube.VideoStats(120, 7, 2, "unlisted")} if found is None else found
        reports = report or (lambda *a, **k: (days if k.get("dimensions") == "day" else total))
        with mock.patch.object(youtube, "access_token", return_value="access"), \
                mock.patch.object(youtube, "video_stats", return_value=found) as stats, \
                mock.patch.object(youtube, "analytics_report", side_effect=reports) as analytics:
            result = youtube_stats.refresh(self.conn, self.cfg, self.source_id, today=TODAY)
        self.stats_call, self.analytics_call = stats, analytics
        return result

    def row(self, upload_id: int) -> dict:
        return dict(self.conn.execute("select * from short_stats where upload_id = %s", (upload_id,)).fetchone())


class RefreshTest(StatsTestCase):
    def test_now_counts_and_analytics_are_stored(self):
        result = self.run_refresh(total=totals(), days=daily("2026-11-01", "2026-11-02"))
        row = self.row(self.first)
        self.assertEqual((row["found"], row["view_count"], row["privacy"]), (True, 120, "unlisted"))
        self.assertEqual((row["views"], row["engaged_views"], row["avg_view_pct"]), (40, 30, 75.5))
        self.assertEqual(row["analytics_through"], dt.date(2026, 11, 2))
        self.assertEqual((result["refreshed"], result["through"], result["calls"]), (1, "2026-11-02", 3))
        days = self.conn.execute("select day from short_stats_daily order by day").fetchall()
        self.assertEqual([d["day"] for d in days], [dt.date(2026, 11, 1), dt.date(2026, 11, 2)])

    def test_the_dates_are_pacific_from_the_upload_day(self):
        # 2026-11-01 03:00 UTC 는 태평양으로 10월 31일 저녁이다. 🔴 한국·UTC 날짜로 시작하면 첫날 조회가 빠진다.
        self.run_refresh(total=totals(), days=[])
        first = self.analytics_call.call_args_list[0]
        self.assertEqual(first.args[1:3], ("2026-10-31", "2026-11-03"))
        self.assertEqual(first.kwargs["filters"], "video==v1")
        self.assertEqual(self.analytics_call.call_args_list[1].kwargs["dimensions"], "day")

    def test_no_processed_days_yet_is_empty_not_zero(self):
        # 🔴 48~72시간 지연. 올린 지 이틀이 안 되면 분석이 비는 게 정상이다 — 0회로 보이면 거짓말이다.
        self.run_refresh(total=[], days=[])
        row = self.row(self.first)
        self.assertEqual((row["view_count"], row["views"], row["analytics_through"]), (120, None, None))

    def test_a_short_gone_from_youtube_is_marked_and_not_analysed(self):
        result = self.run_refresh(found={})
        self.assertEqual(self.row(self.first)["found"], False)
        self.analytics_call.assert_not_called()
        self.assertEqual(result["missing"], 1)

    def test_shorts_on_another_channel_are_skipped(self):
        self.upload("v2", channel="UCother")
        self.conn.commit()
        result = self.run_refresh(total=totals(), days=[])
        self.assertEqual(self.stats_call.call_args.args[1], ["v1"])
        self.assertEqual(result["otherChannel"], 1)
        shown = youtube_stats.view(self.conn, self.source_id)["shorts"]
        self.assertEqual([(s["videoId"], s["otherChannel"]) for s in shown], [("v2", True), ("v1", False)])

    def test_refreshing_again_overwrites(self):
        self.run_refresh(total=totals(views=40), days=daily("2026-11-01"))
        self.run_refresh(total=totals(views=55), days=daily("2026-11-01"))
        self.assertEqual(self.row(self.first)["views"], 55)
        self.assertEqual(self.conn.execute("select count(*) as n from short_stats_daily").fetchone()["n"], 1)

    def test_a_failure_changes_nothing_and_is_recorded(self):
        self.run_refresh(total=totals(views=40), days=[])
        failing = mock.Mock(side_effect=youtube.YoutubeError("Cloud 콘솔에서 이 API 를 사용 설정하지 않았다"))
        with self.assertRaises(youtube.YoutubeError):
            self.run_refresh(report=failing)
        self.assertEqual(self.row(self.first)["views"], 40)
        calls = self.conn.execute("select error from stage_calls where stage = 'analytics' order by id").fetchall()
        self.assertEqual(calls[0]["error"], None)
        self.assertIn("사용 설정", calls[1]["error"])

    def test_an_unknown_channel_id_is_looked_up_first(self):
        self.conn.execute("update youtube_channel set channel_id = null, channel_title = null")
        self.conn.commit()
        with mock.patch.object(youtube, "my_channel", return_value=("UCme", "내 채널")) as mine:
            self.run_refresh(total=totals(), days=[])
        mine.assert_called_once_with("access")
        self.assertEqual(youtube_channel.channel(self.conn)["channel_id"], "UCme")


class RefuseTest(StatsTestCase):
    def test_without_the_analytics_scopes_nothing_is_fetched(self):
        youtube_channel.save_channel(self.conn, youtube.Grant(refresh_token="r", scopes=youtube.UPLOAD_SCOPE))
        self.conn.commit()
        with mock.patch.object(youtube, "access_token") as token:
            with self.assertRaises(youtube_stats.StatsError) as caught:
                youtube_stats.refresh(self.conn, self.cfg, self.source_id)
        token.assert_not_called()
        self.assertIn("다시 연결", str(caught.exception))

    def test_a_source_without_uploads_is_refused(self):
        self.conn.execute("update clip_uploads set status = 'FAILED'")
        self.conn.commit()
        with self.assertRaises(youtube_stats.StatsError):
            youtube_stats.check_ready(self.conn, self.cfg, self.source_id)


class ViewTest(StatsTestCase):
    def test_before_any_refresh_the_shorts_are_listed_without_stats(self):
        shown = youtube_stats.view(self.conn, self.source_id)
        self.assertEqual(shown["shorts"][0]["stats"], None)
        self.assertEqual(shown["shorts"][0]["shortsUrl"], "https://youtube.com/shorts/v1")
        self.assertEqual((shown["totals"], shown["through"]), (None, None))
        self.assertEqual(shown["channel"], {"connected": True, "canAnalytics": True, "channelTitle": "내 채널"})

    def test_totals_add_up_what_adds_and_leave_averages_out(self):
        second = self.upload("v2")
        self.conn.commit()
        found = {"v1": youtube.VideoStats(120, 7, 2, "unlisted"), "v2": youtube.VideoStats(30, 1, 0, "public")}
        self.run_refresh(found=found, total=totals(views=40), days=daily("2026-11-02"))
        shown = youtube_stats.view(self.conn, self.source_id)
        self.assertEqual((shown["totals"]["viewCount"], shown["totals"]["views"]), (150, 80))
        self.assertNotIn("avgViewPct", shown["totals"])
        by_id = {s["uploadId"]: s for s in shown["shorts"]}
        self.assertEqual(by_id[second]["stats"]["avgViewPct"], 75.5)
        self.assertEqual(by_id[second]["daily"], [{"day": "2026-11-02", "views": 10, "engagedViews": 8}])
        self.assertEqual(shown["through"], "2026-11-02")

    def test_a_deleted_clip_keeps_its_short_on_the_source(self):
        # clip_id 가 null 이 돼도(다시 추출) source_id 로 남는다 — 마이그레이션 018.
        self.conn.execute("update clip_uploads set clip_id = null")
        self.assertEqual(len(youtube_stats.view(self.conn, self.source_id)["shorts"]), 1)


class SeparationTest(StatsTestCase):
    def test_it_does_not_read_our_viewer_funnel(self):
        # 🔴 사용자 결정(2026-11-01): 시청자 페이지는 없어질 수 있다. 유튜브 성과는 우리 퍼널과 섞지 않는다.
        # 머리 주석은 그 이유를 적느라 이름을 말한다 — 코드만 본다.
        code = inspect.getsource(youtube_stats).replace(youtube_stats.__doc__, "")
        self.assertNotIn("viewer_events", code)
        modules = {getattr(value, "__name__", "") for value in vars(youtube_stats).values()}
        self.assertNotIn("shorts_maker.answers.events", modules)
