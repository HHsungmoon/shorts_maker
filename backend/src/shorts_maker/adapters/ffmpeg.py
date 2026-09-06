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


def video_fps(src: str, binary: str = "ffmpeg") -> float:
    """영상의 프레임률. concat 필터는 입력들의 프레임률이 같아야 하므로, 만들어 넣는
    브릿지 카드를 원본에 맞춘다 — 원본을 변환하는 것보다 낫다.

    🔴 `binary` 에서 ffprobe 경로를 유도하지 않는다. `/opt/.../ffmpeg-full/bin/ffmpeg` 에서
    문자열 치환을 하면 경로 중간까지 바뀌어 없는 파일을 가리킨다(2026-09-06에 겪었다).
    이 레포는 ffprobe 를 PATH 에서 찾는다(probe 참고) — 메타데이터 조회에는 특별한 빌드가 필요 없다.
    """
    try:
        streams = probe(src).get("streams") or []
    except FfmpegError:
        return 30.0
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        numerator, _, denominator = str(stream.get("r_frame_rate", "")).partition("/")
        try:
            fps = float(numerator) / float(denominator or 1)
        except (ValueError, ZeroDivisionError):
            break
        # 이상치는 믿지 않는다 — 가변 프레임률 소스가 1000 같은 값을 주기도 한다.
        return fps if 1.0 <= fps <= 120.0 else 30.0
    return 30.0


# 9:16 블러 레터박스 체인. 단일 컷과 조합 클립이 **같은 모양**이어야 해서 한 곳에 둔다.
def _vertical_chain(label_in: str, label_out: str, blur: int, fps: float) -> str:
    return (
        f"[{label_in}]split=2[bg{label_out}][fg{label_out}];"
        f"[bg{label_out}]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,boxblur={blur}:5[bgb{label_out}];"
        f"[fg{label_out}]scale=1080:-2[fgs{label_out}];"
        f"[bgb{label_out}][fgs{label_out}]overlay=(W-w)/2:(H-h)/2,"
        # 🔴 concat 필터는 입력들의 해상도·SAR·프레임률이 같아야 한다. 여기서 맞춰 둔다.
        f"setsar=1,fps={fps:g}[{label_out}]"
    )


def render_parts(
    src: str,
    dst: str,
    parts: list[tuple[float, float]],
    bridge_sec: float = 0.4,
    blur: int = 40,
    subtitle_path: str | None = None,
    binary: str = "ffmpeg",
) -> None:
    """여러 조각을 이어붙여 9:16 로 렌더한다. 조각 사이에는 검은 브릿지 카드가 들어간다.

    **한 번의 인코딩으로 끝낸다.** 조각을 따로 렌더해서 concat 데뮤서로 붙이는 방법도 있지만,
    그러면 조각들의 코덱 파라미터가 하나라도 어긋날 때 `-c copy` 가 조용히 싱크가 틀어진 파일을
    만든다. 필터로 붙이면 그 위험이 없고 인코딩도 한 번이다.

    🔴 자막은 **이어붙인 뒤** 한 번만 얹는다. 그래서 자막 시각이 이어붙인 타임라인 기준이어야
    한다(subtitles.build_part_cues 가 브릿지 길이까지 더해 오프셋을 계산한다). 브릿지 카드의
    글자도 같은 자막 파일에 들어 있다 — 별도 필터가 필요 없다.
    """
    if not parts:
        raise FfmpegError("조각이 없다")
    fps = video_fps(src, binary)

    args = [binary, "-nostdin", "-y"]
    for start, end in parts:
        args += ["-ss", f"{start}", "-t", f"{end - start}", "-i", src]
    bridges = len(parts) - 1
    if bridges:
        # 무한 소스 하나를 trim 으로 잘라 쓴다. 브릿지마다 입력을 만들면 인자가 길어지기만 한다.
        args += ["-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r={fps:g}"]
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]

    chain: list[str] = []
    stream_order: list[str] = []
    for index in range(len(parts)):
        chain.append(_vertical_chain(f"{index}:v", f"v{index}", blur, fps))
        # 조각마다 원본 오디오의 샘플레이트가 같더라도 명시해 둔다 — concat 이 요구한다.
        chain.append(f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo[a{index}]")
        stream_order += [f"[v{index}]", f"[a{index}]"]
        if index < bridges:
            color, silence = len(parts), len(parts) + 1
            chain.append(
                f"[{color}:v]trim=duration={bridge_sec},setpts=PTS-STARTPTS,setsar=1[bv{index}]"
            )
            chain.append(
                f"[{silence}:a]atrim=duration={bridge_sec},asetpts=PTS-STARTPTS[ba{index}]"
            )
            stream_order += [f"[bv{index}]", f"[ba{index}]"]

    pieces = len(parts) + bridges
    chain.append(f"{''.join(stream_order)}concat=n={pieces}:v=1:a=1[cv][ca]")
    if subtitle_path:
        chain.append(f"[cv]ass='{_escape_filter_path(subtitle_path)}'[vout]")
        video_label = "[vout]"
    else:
        video_label = "[cv]"

    args += [
        "-filter_complex", ";".join(chain),
        "-map", video_label, "-map", "[ca]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        dst,
    ]
    _run(args, timeout=1800)
