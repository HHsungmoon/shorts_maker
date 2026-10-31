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
from urllib.parse import parse_qs, urlparse

from .. import config

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
}

# 파일명은 영상 id 로 짓는다. 제목을 쓰면 한글·특수문자가 셸과 컨테이너 마운트를 오가며
# 깨진다(실제로 겪었다).
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{5,64}$")


class DownloadError(RuntimeError):
    pass


# 🔴 유튜브는 **데이터센터 IP 를 막는다**(2026-09-17 운영에서 겪었다). 같은 영상이 맥에서는
# 받아지고 서버에서만 `Video unavailable` 로 죽었다 — 영상이 내려간 게 아니라 **주소가 막힌 것**이다.
# yt-dlp 원문만 보여주면 사람은 영상을 의심하고 다른 URL 을 계속 넣어 본다. 그래서 여기서 번역한다.
#
# 표식을 넓게 잡지 않는다 — 진짜로 없는 영상까지 "IP 문제" 로 안내하면 반대 방향으로 헤매게 된다.
BLOCKED_SIGNS = (
    "video unavailable",
    "sign in to confirm you're not a bot",
    "confirm you're not a bot",
    "this content isn't available",
    "failed to extract any player response",
)

BLOCKED_HINT = (
    "데이터센터 IP 로는 유튜브 영상 다운로드가 어렵습니다. "
    "영상을 직접 내려받아 서버의 sources 폴더에 올린 뒤 '서버에 저장된 파일' 로 등록해 주세요."
)


def _blocked(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(sign in lowered for sign in BLOCKED_SIGNS)


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


def parse_video_id(raw: str) -> str:
    """사람이 준 값에서 영상 id 만 뽑는다.

    🔴 **URL 을 그대로 저장하면 임베드가 조용히 깨진다** — 플레이어는 id 만 받는다. 등록 경로에서는
    yt-dlp 가 id 를 주지만 손으로 채우는 자리(파일로 올린 원본)는 사람이 URL 을 붙여넣는다.
    받아 주는 편이 "id 만 넣으세요" 라고 적어 두는 것보다 확실하다.
    """
    value = raw.strip()
    if ID_PATTERN.match(value):
        return value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host in ALLOWED_HOSTS:
        # youtu.be/<id> · watch?v=<id> · shorts/<id> · embed/<id> 를 모두 같은 자리로 모은다.
        candidate = parse_qs(parsed.query).get("v", [""])[0] or parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if ID_PATTERN.match(candidate):
            return candidate
    raise DownloadError(f"유튜브 영상 id 를 읽지 못했다: {raw!r}")


def _run(args: list[str], timeout: int) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise DownloadError("yt-dlp 가 없다") from exc
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(f"시간 초과({timeout}s)") from exc
    if done.returncode != 0:
        detail = done.stderr.strip()[-400:]
        # 원문을 버리지 않는다 — 안내는 추측이고, 진짜 원인은 뒤에 붙은 yt-dlp 의 말에 있다.
        if _blocked(done.stderr):
            raise DownloadError(f"{BLOCKED_HINT} (yt-dlp: {detail})")
        raise DownloadError(f"yt-dlp 실패: {detail}")
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


# ---------------------------------------------------------------- 라이브 채팅 다시보기 (tease §3-5)
#
# 🔴 끝난 라이브의 채팅은 공식 API(`liveChatMessages`)로 못 받는다 — 방송 중에만 읽힌다. 그래서 yt-dlp 의
# `live_chat` 자막 트랙을 쓴다. 정책 부채는 영상 받기와 같다(tease §7-5).


class NoLiveChat(DownloadError):
    """채팅 다시보기가 없다 — 라이브가 아니었거나, 채팅을 껐거나, 아직 다시보기가 안 만들어졌다."""


@dataclass(frozen=True)
class ChatMessage:
    id: str
    # 영상 안의 초. 방송 시작 전 대기실 채팅은 0 으로 온다.
    offset_sec: float
    text: str
    # 작성자 채널 id. 🔴 **저장하지 않는다** — 부르는 쪽(answers/livechat)이 해시만 남긴다.
    author_id: str
    # 채널 주인이거나 주인·모더레이터 표식이 있다. 🔴 다시보기에는 표식이 빠져 오는 일이 많다
    # (쏘카 LIVE 2026-10-31: 464개 중 표식 0) — 표식만 믿으면 안 된다(answers/livechat.ANNOUNCER_REPEATS).
    staff: bool


def fetch_live_chat(cfg: config.Config, video_id: str) -> tuple[Path, str]:
    """채팅 다시보기 파일과 그 영상의 채널 id 를 돌려준다.

    🔴 파일에는 **작성자 이름이 들어 있다.** 부르는 쪽이 읽고 바로 지운다(answers/livechat).
    """
    if not ID_PATTERN.match(video_id):
        raise DownloadError(f"영상 id 가 이상하다: {video_id!r}")
    folder = cfg.work_dir / "livechat"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{video_id}.live_chat.json"
    # 남아 있으면 yt-dlp 가 "이미 있다" 며 건너뛴다. 지난번 것을 읽으면 그사이 바뀐 것을 놓친다.
    target.unlink(missing_ok=True)
    # `-O` 는 원래 아무것도 안 받는 모드다. `--no-simulate` 로 자막(=채팅)은 받으면서 채널 id 를 같이 찍는다.
    out = _run(
        [
            "yt-dlp", "--no-playlist", "--skip-download", "--no-simulate",
            "--write-subs", "--sub-langs", "live_chat",
            "-O", "%(channel_id)s",
            "-o", str(folder / video_id),
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        # 96분 방송 채팅 464개가 약 20초(2026-10-31). 몇 시간짜리 방송을 넉넉히 덮는다.
        timeout=1800,
    )
    if not target.is_file():
        raise NoLiveChat("이 영상에는 라이브 채팅 다시보기가 없습니다(라이브가 아니었거나 채팅이 꺼져 있었습니다)")
    return target, out.strip().splitlines()[-1].strip() if out.strip() else ""


def parse_live_chat(path: Path, owner_id: str) -> list[ChatMessage]:
    """한 줄이 JSON 하나인 다시보기 파일에서 **글 메시지만** 꺼낸다.

    슈퍼챗·참여 안내(liveChatViewerEngagementMessageRenderer) 같은 것은 뺀다. 이모지는 글자가 아니라
    그림 조각으로 오는데 질문 문장에는 쓸모가 없어 버린다.
    """
    messages: list[ChatMessage] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            replay = json.loads(line).get("replayChatItemAction") or {}
        except json.JSONDecodeError:
            # 한 줄이 깨져도 나머지는 읽는다 — 줄 단위 파일이라 앞뒤가 서로 기대지 않는다.
            continue
        try:
            offset = max(0.0, int(replay.get("videoOffsetTimeMsec") or 0) / 1000)
        except (TypeError, ValueError):
            offset = 0.0
        for action in replay.get("actions") or []:
            item = ((action.get("addChatItemAction") or {}).get("item") or {})
            renderer = item.get("liveChatTextMessageRenderer")
            if not renderer or not renderer.get("id"):
                continue
            text = "".join(run.get("text", "") for run in (renderer.get("message") or {}).get("runs") or [])
            author = str(renderer.get("authorExternalChannelId") or "")
            badges = {
                ((badge.get("liveChatAuthorBadgeRenderer") or {}).get("icon") or {}).get("iconType")
                for badge in renderer.get("authorBadges") or []
            }
            messages.append(ChatMessage(
                id=str(renderer["id"]),
                offset_sec=offset,
                text=text.strip(),
                author_id=author,
                staff=bool(owner_id and author == owner_id) or bool(badges & {"OWNER", "MODERATOR"}),
            ))
    return messages


# ---------------------------------------------------------------- 자막 (tease §5-1, 2026-11-01)


class NoCaptions(DownloadError):
    """그 언어의 자막 트랙이 없다. 원티드 영상은 화면 안에 자막을 구워 넣은 것이 많다 — 그러면 트랙이 없다."""


@dataclass(frozen=True)
class CaptionWord:
    # 원본 절대 초.
    start: float
    end: float
    # 자막에 적힌 그대로(앞 공백 · 문장부호 포함). 🔴 `>>` 는 여기서 떼고 `speaker_change` 로 옮긴다.
    text: str
    # 이 단어부터 다른 사람이 말한다. 대담 영상에서 자동자막이 `>>` 로 표시한다.
    speaker_change: bool


# 단어의 끝을 이보다 길게 잡지 않는다. json3 는 단어의 **시작만** 준다 — 끝은 다음 단어의 시작이 기본인데,
# 말이 끊긴 뒤 다음 단어가 10초 뒤에 오면 마지막 단어가 10초짜리가 되고 자막이 그만큼 화면에 머문다.
# 한국어 어절 하나는 실측(삼성 31분 whisper 단어)에서 대부분 0.2~1.2초다.
MAX_WORD_SEC = 1.5


def fetch_captions(cfg: config.Config, video_id: str, language: str) -> Path:
    """그 언어의 자막을 json3 로 받아 파일 경로를 돌려준다. 부르는 쪽이 읽고 지운다.

    사람이 올린 자막이 있으면 그것을, 없으면 자동자막을 받는다 — 둘을 다 요청하면 yt-dlp 가 사람 자막을
    고른다. 🔴 VTT 가 아니라 json3 다(update_plan D2): 자동자막 VTT 는 롤링 표시라 타임스탬프가 겹친다.
    """
    if not ID_PATTERN.match(video_id):
        raise DownloadError(f"영상 id 가 이상하다: {video_id!r}")
    if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", language):
        raise DownloadError(f"언어 코드가 이상하다: {language!r}")
    folder = cfg.work_dir / "captions"
    folder.mkdir(parents=True, exist_ok=True)
    # 남아 있으면 yt-dlp 가 "이미 있다" 며 건너뛴다. 지난번 것을 읽으면 그사이 고친 자막을 놓친다.
    for old in folder.glob(f"{video_id}.*.json3"):
        old.unlink(missing_ok=True)
    _run(
        [
            "yt-dlp", "--no-playlist", "--skip-download",
            "--write-subs", "--write-auto-subs", "--sub-langs", language, "--sub-format", "json3",
            "-o", str(folder / video_id),
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        # 134분 영상 자막이 1초 안쪽(2026-11-01). 네트워크가 느려도 넉넉하다.
        timeout=300,
    )
    found = sorted(folder.glob(f"{video_id}.*.json3"))
    if not found:
        raise NoCaptions(
            f"이 영상에는 '{language}' 자막이 없습니다. 화면 안에 자막이 구워진 영상일 수 있습니다 — [음성 인식] 을 쓰세요"
        )
    return found[0]


def parse_json3(path: Path) -> list[CaptionWord]:
    """json3 자막을 단어 목록으로. 시간 순서대로다.

    자동자막은 한 줄(event)에 단어 여럿이 `tOffsetMs` 와 함께 온다. 사람이 올린 자막은 줄 하나가 통째로
    한 조각이다 — 그때는 줄을 단어 하나로 둔다. 🔴 줄 안에서 단어별 시각을 나눠 지어내지 않는다(§12).
    줄바꿈만 든 줄(`aAppend`)·빈 조각은 버린다.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DownloadError(f"자막 파일을 읽을 수 없습니다: {exc}") from exc

    # (시작, 그 줄의 끝, 글자, 말하는 사람 바뀜, 자동자막 단어인가)
    raw: list[tuple[float, float, str, bool, bool]] = []
    for event in data.get("events") or []:
        segs = event.get("segs") or []
        start_ms = event.get("tStartMs")
        if not segs or start_ms is None:
            continue
        line_end = (start_ms + (event.get("dDurationMs") or 0)) / 1000
        for seg in segs:
            text = seg.get("utf8") or ""
            if not text.strip():
                continue
            change = bool(seg.get("isSpeakerChange"))
            stripped = text.lstrip()
            if stripped.startswith(">>"):
                change = True
                text = " " + stripped[2:].lstrip()
                if not text.strip():
                    continue
            # 자동자막 조각에만 인식 신뢰도 키가 붙는다. 사람 자막의 조각은 줄 하나라 단어 길이 상한을 걸면 안 된다.
            is_word = "acAsrConf" in seg
            raw.append(((start_ms + (seg.get("tOffsetMs") or 0)) / 1000, line_end, text, change, is_word))

    raw.sort(key=lambda item: item[0])
    words: list[CaptionWord] = []
    for index, (start, line_end, text, change, is_word) in enumerate(raw):
        following = raw[index + 1][0] if index + 1 < len(raw) else line_end
        end = min(following, line_end, start + MAX_WORD_SEC if is_word else line_end)
        # 시작이 같은 단어가 둘이면 끝이 시작과 같아진다. 0초 단어는 번인 자막에서 사라진다(subtitles) —
        # 최소 길이만 준다. 지어낸 초가 아니라 "이 단어는 여기서 시작했다" 를 지키는 쪽이다.
        words.append(CaptionWord(round(start, 3), round(max(end, start + 0.05), 3), text, change))
    return words
