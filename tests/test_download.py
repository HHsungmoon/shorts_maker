"""URL 가드 검증.

🔴 서버가 임의 URL 을 받게 하면 내부망(postgres:5432, 메타데이터 서버)을 찌를 수 있다.
호스트 제한이 그 통로를 닫는 유일한 장치라 여기서 고정한다.
"""

import unittest

from shorts_maker import download


class CheckUrlTest(unittest.TestCase):
    def test_accepts_youtube(self):
        for url in (
            "https://www.youtube.com/watch?v=s7Cuv-ErQHk",
            "https://youtu.be/s7Cuv-ErQHk",
            "https://m.youtube.com/watch?v=s7Cuv-ErQHk",
        ):
            with self.subTest(url=url):
                self.assertEqual(download.check_url(url), url)

    def test_rejects_other_hosts(self):
        for url in (
            "https://evil.example.com/x.mp4",
            "http://169.254.169.254/latest/meta-data/",
            "http://postgres:5432/",
            "https://youtube.com.evil.example.com/watch?v=1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(download.DownloadError):
                    download.check_url(url)

    def test_rejects_non_http_schemes(self):
        for url in ("file:///etc/passwd", "ftp://youtube.com/x", "gopher://youtube.com/"):
            with self.subTest(url=url):
                with self.assertRaises(download.DownloadError):
                    download.check_url(url)

    def test_video_id_pattern_rejects_path_tricks(self):
        # 파일명이 id 로 지어지므로 여기가 뚫리면 경로 탈출이 된다.
        for bad in ("../../etc/passwd", "a/b", "with space", ""):
            with self.subTest(bad=bad):
                self.assertIsNone(download.ID_PATTERN.match(bad))


if __name__ == "__main__":
    unittest.main()
