"""ffmpeg/ffprobe 는 바이너리를 subprocess 로만 부른다(문서 §12).

라이브러리 래퍼를 쓰지 않는 이유: 필터체인과 코덱 옵션을 전부 못 쓰고, 영상 길이에
비례해 느려진다. 여기서 필요한 건 인자 조립뿐이라 래퍼가 주는 게 없다.
"""

import json
import subprocess


class FfmpegError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 600) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise FfmpegError(f"{args[0]} 를 찾을 수 없다 (brew install ffmpeg)") from exc
    if done.returncode != 0:
        raise FfmpegError(f"{args[0]} 실패 (exit {done.returncode}): {done.stderr.strip()[-500:]}")
    return done.stdout


def has_filter(name: str, binary: str = "ffmpeg") -> bool:
    """그 빌드에 해당 필터가 있는지 본다.

    🔴 Homebrew 의 기본 `ffmpeg` 는 libass 없이 빌드돼 `ass`/`subtitles`/`drawtext` 가
    아예 없다. 자막 번인이 조용히 빠지는 대신 여기서 걸러 이유를 알려준다.
    """
    try:
        listing = _run([binary, "-hide_banner", "-filters"], timeout=15)
    except FfmpegError:
        return False
    return any(line.split()[1:2] == [name] for line in listing.splitlines() if line.strip())


def version(binary: str = "ffmpeg") -> str:
    # `-version` 첫 줄이 "ffmpeg version 9.0.1 Copyright ..." 형태다.
    first_line = _run([binary, "-version"], timeout=15).splitlines()[0]
    return first_line.split(" Copyright", 1)[0].strip()


def probe(path: str) -> dict:
    out = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        timeout=60,
    )
    return json.loads(out)


def duration_sec(path: str) -> float:
    return float(probe(path)["format"]["duration"])


def extract_audio(src: str, dst: str, start: float, end: float) -> None:
    """구간 오디오를 16kHz mono wav 로 뽑는다.

    16kHz mono 인 이유: whisper 계열이 내부에서 어차피 이 형식으로 리샘플한다. 미리 맞춰두면
    STT 가 매번 디코딩하지 않고, 파일도 작아진다(30분 ≈ 58MB).
    `-ss` 를 `-i` 앞에 두면 빠른 탐색 후 정확한 지점까지 맞춰준다 — 오디오 재인코딩이라
    샘플 단위로 정확하다.
    """
    _run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-ss", f"{start}", "-i", src, "-t", f"{end - start}",
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            dst,
        ],
        timeout=1800,
    )


def _escape_filter_path(path: str) -> str:
    # filter_complex 안에서 경로의 `\` `:` `'` 는 구분자로 먹힌다.
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_vertical(
    src: str,
    dst: str,
    start: float,
    end: float,
    blur: int = 40,
    subtitle_path: str | None = None,
    binary: str = "ffmpeg",
) -> None:
    """16:9 원본에서 구간을 잘라 9:16 블러 레터박스로 렌더한다(문서 §4-[7]).

    배경은 원본을 9:16 에 꽉 차게 확대해 잘라낸 뒤 블러를 먹이고, 그 위에 원본 비율을
    유지한 영상을 중앙에 얹는다. 피사체 추적 크롭은 나중이다 — 강연은 발표자가 화면
    중앙에 고정돼 있어서 이것만으로 충분히 확인된다.

    🔴 분석용 저화질본이 아니라 **원본에서 다시 자른다**. 오디오 청크(16kHz mono)는
    STT 입력일 뿐이고, 여기서는 원본의 영상·오디오를 쓴다.

    `-ss` 를 `-i` 앞에 두고 재인코딩하므로 시작 지점이 정확하다 — 키프레임에 스냅되지 않는다.
    """
    chain = (
        "[0:v]split=2[bg][fg];"
        f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur={blur}:5[bgb];"
        "[fg]scale=1080:-2[fgs];"
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
    )
    if subtitle_path:
        # 자막의 시각은 잘라낸 뒤의 타임라인 기준이다(subtitles.build_cues 가 상대 초로 만든다).
        chain += f"[v];[v]ass='{_escape_filter_path(subtitle_path)}'"
    _run(
        [
            binary, "-nostdin", "-y",
            "-ss", f"{start}", "-i", src, "-t", f"{end - start}",
            "-filter_complex", chain,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            # 웹에서 스트리밍 재생하려면 moov 가 앞에 있어야 한다 — 관리자 페이지가 이걸 그대로 튼다.
            "-movflags", "+faststart",
            dst,
        ],
        timeout=1800,
    )


def extract_frame(src: str, dst: str, at: float, binary: str = "ffmpeg") -> None:
    _run([binary, "-nostdin", "-y", "-ss", f"{at}", "-i", src, "-frames:v", "1", dst], timeout=120)


def copy_segment(src: str, dst: str, start: float, end: float, binary: str = "ffmpeg") -> None:
    """구간을 재인코딩 없이 그대로 떠낸다 — 원본 16:9 미리보기용.

    `-c copy` 라 몇 초면 끝난다. 대신 **키프레임에 스냅돼 시작이 몇 초 앞당겨질 수 있다** —
    미리보기에는 문제가 없고, 실제 클립은 [7] 에서 정확한 지점부터 재인코딩한다.
    """
    _run(
        [
            binary, "-nostdin", "-y",
            "-ss", f"{start}", "-i", src, "-t", f"{end - start}",
            "-c", "copy", "-movflags", "+faststart",
            dst,
        ],
        timeout=600,
    )


def font_available(family: str) -> bool | None:
    """그 폰트가 실제로 있는지 fontconfig 에 묻는다. fc-match 가 없으면 None(모름).

    🔴 libass 는 폰트를 못 찾아도 실패하지 않는다 — 아무 폰트로 폴백해 **두부(□)를 그린다.**
    결과 영상만 봐서는 "자막이 안 나온다"가 아니라 "자막이 깨졌다"로 보여서 원인을 찾기 어렵다.
    렌더 전에 여기서 걸러 이유를 말해준다.
    """
    try:
        matched = _run(["fc-match", "-f", "%{family}", family], timeout=15)
    except FfmpegError:
        return None
    # fc-match 는 못 찾아도 대체 폰트를 돌려준다. 요청한 이름이 결과에 없으면 폴백된 것이다.
    wanted = family.replace(" ", "").lower()
    return any(wanted == part.replace(" ", "").lower() for part in matched.split(","))


def detect_silences(
    src: str, noise_db: float = -32.0, min_sec: float = 0.6, binary: str = "ffmpeg"
) -> list[tuple[float, float]]:
    """무음 구간 [(start, end), …]. 청크 경계를 말 중간이 아닌 곳에 놓는 데 쓴다.

    영상 전체를 한 번 훑으므로 95분이면 수십 초가 든다 — 등록 직후 한 번만 부른다.
    실패하면 빈 목록을 준다: 경계가 조금 나빠질 뿐 분할 자체는 되어야 한다.

    `-vn` 으로 영상을 건너뛴다. 오디오만 디코드하면 훨씬 빠르다.
    """
    import re

    import logging

    try:
        done = subprocess.run(
            [binary, "-nostdin", "-hide_banner", "-vn", "-i", src,
             "-af", f"silencedetect=noise={noise_db}dB:d={min_sec}", "-f", "null", "-"],
            capture_output=True, text=True, timeout=1800,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logging.warning("silencedetect 를 돌리지 못했다 (%s) — 청크 경계를 고정 길이로 자른다", exc)
        return []
    if done.returncode != 0:
        # 🔴 조용히 빈 목록을 주면 경계가 나빠진 채로 굴러가고 아무도 모른다. 파일 경로가 틀린
        # 설정 실수가 여기로 떨어진 적이 있다(2026-09-06).
        logging.warning(
            "silencedetect 실패 (exit %s) — 청크 경계를 고정 길이로 자른다: %s",
            done.returncode, done.stderr.strip()[-200:],
        )
        return []
    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", done.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*(-?[\d.]+)", done.stderr)]
    return [(a, b) for a, b in zip(starts, ends) if b > a]
