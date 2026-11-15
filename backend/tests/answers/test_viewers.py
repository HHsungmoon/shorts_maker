"""방문자 IP 를 어느 헤더에서 읽나(viewers.client_ip). DB 가 필요 없다.

🔴 지키는 것: 배포가 고른 헤더 **하나만** 믿는다. 터널 뒤에서 X-Real-IP 를 믿으면 방문자가 그 헤더를
적어 레이트리밋과 로그인 잠금을 피한다 — Cloudflare 는 그 헤더를 덮어쓰지 않는다.
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from starlette.requests import Request

from shorts_maker import config
from shorts_maker.answers import viewers
from shorts_maker.http import deps
from ..support import make_config


def _request(headers: dict[str, str], peer: str = "172.18.0.5") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 40000)})


class ClientIpTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = deps.cfg
        self.addCleanup(lambda: setattr(deps, "cfg", self._saved))
        deps.cfg = make_config(Path(self._tmp.name))

    def use(self, header: str) -> None:
        deps.cfg = dataclasses.replace(deps.cfg, client_ip_header=header)

    def test_behind_nginx_reads_x_real_ip(self):
        self.use("x-real-ip")
        self.assertEqual(viewers.client_ip(_request({"X-Real-IP": "203.0.113.7"})), "203.0.113.7")

    def test_behind_tunnel_reads_cf_connecting_ip(self):
        self.use("cf-connecting-ip")
        self.assertEqual(viewers.client_ip(_request({"CF-Connecting-IP": "198.51.100.4"})), "198.51.100.4")

    def test_behind_tunnel_ignores_a_forged_x_real_ip(self):
        # Cloudflare 는 X-Real-IP 를 손대지 않고 넘긴다 — 방문자가 적은 값이 그대로 온다.
        self.use("cf-connecting-ip")
        forged = _request({"X-Real-IP": "10.9.9.9", "CF-Connecting-IP": "198.51.100.4"})
        self.assertEqual(viewers.client_ip(forged), "198.51.100.4")

    def test_behind_nginx_ignores_a_forged_cf_connecting_ip(self):
        self.use("x-real-ip")
        forged = _request({"X-Real-IP": "203.0.113.7", "CF-Connecting-IP": "10.9.9.9"})
        self.assertEqual(viewers.client_ip(forged), "203.0.113.7")

    def test_without_the_header_falls_back_to_the_socket(self):
        self.use("cf-connecting-ip")
        self.assertEqual(viewers.client_ip(_request({}, peer="127.0.0.1")), "127.0.0.1")


class ClientIpHeaderConfigTest(unittest.TestCase):
    def test_accepts_the_two_overwritten_headers_in_any_case(self):
        self.assertEqual(config._client_ip_header(" CF-Connecting-IP "), "cf-connecting-ip")
        self.assertEqual(config._client_ip_header("X-Real-IP"), "x-real-ip")

    def test_refuses_x_forwarded_for_and_typos(self):
        # 오타를 받으면 헤더가 늘 비어 전 방문자가 프록시 IP 하나로 묶인다 — 기동 시점에 막는다.
        for bad in ("x-forwarded-for", "cf-connecting-ips", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config._client_ip_header(bad)

    def test_default_is_nginx(self):
        self.assertEqual(config.DEFAULTS["SHORTS_CLIENT_IP_HEADER"], "x-real-ip")


if __name__ == "__main__":
    unittest.main()
