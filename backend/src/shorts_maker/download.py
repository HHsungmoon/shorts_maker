"""URL 로 원본 영상을 받아온다.

서버가 직접 받는 이유는 속도다 — 집 업로드 회선으로 1.3GB 를 올리는 것보다 서버 회선으로
받는 게 훨씬 빠르고, 로컬에 사본을 둘 이유도 없다.

🔴 **유튜브 호스트만 허용한다.** 임의 URL 을 서버가 받게 하면 내부망(postgres:5432,
클라우드 메타데이터 서버 등)을 찌를 수 있다(SSRF). 호스트를 고정하면 그 통로가 닫힌다.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import config

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
}

# 파일명은 영상 id 로 짓는다. 제목을 쓰면 한글·특수문자가 셸과 컨테이너 마운트를 오가며
# 깨진다(실제로 겪었다).
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{5,64}$")


class DownloadError(RuntimeError):
    pass


@dataclass
class VideoInfo:
    video_id: str
    title: str
    duration_sec: float
    uploader: str
    url: str


def check_url(raw: str) -> str:
    parsed = urlparse(raw.strip())
    if parsed.scheme not in ("http", "https"):
        raise DownloadError("http/https URL 만 받는다")
    if parsed.hostname is None or parsed.hostname.lower() not in ALLOWED_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_HOSTS))
        raise DownloadError(f"허용되지 않은 주소다. 허용: {allowed}")
    return raw.strip()


def _run(args: list[str], timeout: int) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise DownloadError("yt-dlp 가 없다") from exc
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(f"시간 초과({timeout}s)") from exc
    if done.returncode != 0:
        raise DownloadError(f"yt-dlp 실패: {done.stderr.strip()[-400:]}")
    return done.stdout


def probe(url: str) -> VideoInfo:
    """받기 전에 메타데이터만 확인한다. 길이를 미리 알아야 청크 범위를 정할 수 있다."""
    raw = _run(
        ["yt-dlp", "--no-playlist", "--skip-download", "--dump-single-json", check_url(url)],
        timeout=120,
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DownloadError("메타데이터를 읽지 못했다") from exc

    video_id = str(data.get("id") or "")
    if not ID_PATTERN.match(video_id):
        raise DownloadError(f"영상 id 가 이상하다: {video_id!r}")
    return VideoInfo(
        video_id=video_id,
        title=str(data.get("title") or video_id),
        duration_sec=float(data.get("duration") or 0),
        uploader=str(data.get("uploader") or ""),
        url=str(data.get("webpage_url") or url),
    )


def target_path(cfg: config.Config, info: VideoInfo) -> Path:
    """받을 파일의 자리. 다운로드 **전에** 알아야 한다 — 원본 행을 RUNNING 으로 먼저 만들 때
    경로가 필요하다(ingest.begin_source)."""
    cfg.source_dir.mkdir(parents=True, exist_ok=True)
    return cfg.source_dir / f"{info.video_id}.mp4"


def fetch_video(cfg: config.Config, info: VideoInfo) -> Path:
    target = target_path(cfg, info)
    if target.is_file():
        return target

    # 1080p 를 넘기지 않는다. 결과가 9:16 1080 폭이라 그 위는 렌더에 쓰이지 않고 디스크만 먹는다.
    _run(
        [
            "yt-dlp", "--no-playlist",
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", str(target),
            info.url,
        ],
        timeout=3600,
    )
    if not target.is_file():
        raise DownloadError("받았지만 파일이 없다")
    return target


def fetch(cfg: config.Config, url: str) -> tuple[Path, VideoInfo]:
    """probe + fetch_video. 행을 먼저 만들 필요가 없는 호출자(CLI 등)용."""
    info = probe(url)
    return fetch_video(cfg, info), info
