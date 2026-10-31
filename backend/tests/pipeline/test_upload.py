"""유튜브 업로드 단계 — 무엇을 보내고 무엇을 남기는가(tease §7-5).

🔴 지키는 것: ① 사람이 쓴 제목·설명을 **고치지 않고 거절한다**(개발자 정책 III.C.3) ② 성공도 실패도
`clip_uploads` 와 `stage_calls` 에 남는다(규약: 모든 단계) ③ 기본 공개 범위는 private 다.
"""

import tempfile
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import youtube
from shorts_maker.pipeline import upload, youtube_channel

from ..support import DbTestCase, make_config


class UploadTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cfg = make_config(root, youtube_client_id="cid", youtube_client_secret="secret")
        (root / "work" / "clips").mkdir(parents=True)
        (root / "work" / "clips" / "c1.mp4").write_bytes(b"\x00" * 2048)
        source = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, youtube_id, language)"
            " values ('2026 채용설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', 'abcDEF12345', 'ko') returning id"
        ).fetchone()["id"]
        chunk = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 900, 'c.wav') returning id",
            (source,),
        ).fetchone()["id"]
        segment = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx)"
            " values (%s, 0, 700, 800, 0, 3) returning id",
            (chunk,),
        ).fetchone()["id"]
        run = self.conn.execute("insert into runs (source_id) values (%s) returning id", (source,)).fetchone()["id"]
        self.clip_id = self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec, path, rendered, title)"
            " values (%s, %s, 754.6, 790, 'clips/c1.mp4', true, '연봉은 어떻게 정하나요?') returning id",
            (run, segment),
        ).fetchone()["id"]
        self.source_id, self.run_id = source, run
        self.conn.commit()

    def connect_channel(self):
        youtube_channel.save_channel(self.conn, youtube.Grant(refresh_token="refresh-1", scopes=youtube.UPLOAD_SCOPE))
        self.conn.commit()


class MetadataTest(UploadTestCase):
    def test_defaults_point_back_to_the_second_the_clip_starts(self):
        defaults = upload.defaults(self.conn, self.clip_id)
        self.assertEqual(defaults["title"], "연봉은 어떻게 정하나요?")
        self.assertEqual(defaults["privacy"], "private")
        # 초는 클립의 start_sec(발화 타임스탬프)에서 내림 — LLM 이 말한 초가 아니다.
        self.assertIn("https://youtu.be/abcDEF12345?t=754", defaults["description"])
        self.assertIn("12:34", defaults["description"])
        self.assertTrue(defaults["description"].endswith("#Shorts"))

    def test_a_source_without_a_youtube_id_gets_no_link(self):
        self.conn.execute("update sources set youtube_id = null")
        self.assertNotIn("youtu.be", upload.defaults(self.conn, self.clip_id)["description"])

    def test_values_are_refused_not_rewritten(self):
        # 🔴 III.C.3 — 100자를 넘는 제목을 조용히 잘라 올리면 사람이 쓴 적 없는 제목이 채널에 남는다.
        for title, description, privacy in (
            ("가" * 101, "", "private"),
            ("   ", "", "private"),
            ("<b>제목</b>", "", "private"),
            ("제목", "설명 >", "private"),
            ("제목", "가" * 1667, "private"),  # 5001바이트
            ("제목", "", "everyone"),
        ):
            with self.subTest(title=title[:10], description=description[:10], privacy=privacy):
                with self.assertRaises(upload.UploadError):
                    upload.check_metadata(title, description, privacy)

    def test_the_edges_pass(self):
        title, description, _ = upload.check_metadata(" " + "가" * 100 + " ", "가" * 1666, "public")
        self.assertEqual(len(title), 100)
        self.assertEqual(len(description.encode()), 4998)

    def test_the_request_marks_a_real_lecture_clip(self):
        body = upload.metadata("제목", "설명", "unlisted", "ko")
        self.assertEqual(body["snippet"]["categoryId"], "27")
        self.assertEqual(body["snippet"]["defaultAudioLanguage"], "ko")
        self.assertEqual(body["status"]["privacyStatus"], "unlisted")
        self.assertIs(body["status"]["containsSyntheticMedia"], False)
        self.assertIs(body["status"]["selfDeclaredMadeForKids"], False)


class ReadyTest(UploadTestCase):
    def test_no_channel_is_not_ready(self):
        with self.assertRaises(upload.NotReady):
            upload.check_ready(self.conn, self.cfg, self.clip_id)

    def test_an_unrendered_clip_is_not_ready(self):
        self.connect_channel()
        self.conn.execute("update clips set rendered = false")
        with self.assertRaises(upload.NotReady):
            upload.check_ready(self.conn, self.cfg, self.clip_id)

    def test_missing_settings_are_not_ready(self):
        self.connect_channel()
        cfg = make_config(Path(self._tmp.name))
        with self.assertRaises(upload.NotReady):
            upload.check_ready(self.conn, cfg, self.clip_id)

    def test_a_missing_clip_is_not_found(self):
        with self.assertRaises(upload.ClipNotFound):
            upload.check_ready(self.conn, self.cfg, 9999)


class RunTest(UploadTestCase):
    def setUp(self):
        super().setUp()
        self.connect_channel()

    def test_a_good_upload_is_recorded_and_names_the_channel(self):
        video = {"id": "vid42", "snippet": {"channelId": "UCme", "channelTitle": "성문 채널"}}
        with mock.patch.object(youtube, "access_token", return_value="tok") as token, \
             mock.patch.object(youtube, "upload", return_value=video) as put:
            result = upload.run(self.conn, self.cfg, self.clip_id, "제목", "설명", "private")
        token.assert_called_once_with(self.cfg, "refresh-1")
        self.assertEqual(put.call_args.args[1], self.cfg.work_file("clips/c1.mp4"))
        # 잡 결과는 업로드 기록 한 줄과 같은 모양이다 — 주소도 거기서 짓는다.
        self.assertEqual(result, upload.history(self.conn, self.clip_id)[0])
        self.assertEqual(result["shorts_url"], "https://youtube.com/shorts/vid42")
        self.assertEqual(result["studio_url"], "https://studio.youtube.com/video/vid42/edit")

        row = self.conn.execute("select * from clip_uploads").fetchone()
        self.assertEqual((row["status"], row["youtube_video_id"], row["privacy"]), ("DONE", "vid42", "private"))
        # 채널 이름은 업로드 응답에서 처음 안다(upload 범위로는 channels.list 를 못 부른다).
        self.assertEqual(youtube_channel.channel(self.conn)["channel_title"], "성문 채널")
        call = self.conn.execute("select * from stage_calls where stage = 'upload'").fetchone()
        self.assertEqual((call["source_id"], call["run_id"], call["error"]), (self.source_id, self.run_id, None))
        self.assertEqual(call["params"]["video_id"], "vid42")
        self.assertEqual(call["params"]["bytes"], 2048)

    def test_a_failed_upload_is_recorded_too(self):
        with mock.patch.object(youtube, "access_token", side_effect=youtube.ReconnectNeeded("만료")), \
             mock.patch.object(youtube, "upload") as put:
            with self.assertRaises(youtube.ReconnectNeeded):
                upload.run(self.conn, self.cfg, self.clip_id, "제목", "설명", "private")
        put.assert_not_called()
        row = upload.history(self.conn, self.clip_id)[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("만료", row["error"])
        # 올라가지 않았으니 갈 곳도 없다.
        self.assertIsNone(row["shorts_url"])
        call = self.conn.execute("select error from stage_calls where stage = 'upload'").fetchone()
        self.assertIn("ReconnectNeeded", call["error"])

    def test_the_history_survives_the_clip(self):
        # 🔴 클립은 "다시 추출" 로 지워진다. 영상은 채널에 남으니 올린 기록도 남아야 한다.
        with mock.patch.object(youtube, "access_token", return_value="tok"), \
             mock.patch.object(youtube, "upload", return_value={"id": "vid7", "snippet": {}}):
            upload.run(self.conn, self.cfg, self.clip_id, "제목", "", "unlisted")
        self.conn.execute("delete from clips")
        row = self.conn.execute("select clip_id, youtube_video_id, title from clip_uploads").fetchone()
        self.assertEqual((row["clip_id"], row["youtube_video_id"], row["title"]), (None, "vid7", "제목"))
