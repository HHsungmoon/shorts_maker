"""환경설정 로딩. 의존성을 늘리지 않으려고 .env 파서를 직접 둔다(10줄이면 끝난다)."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS = {
    "SHORTS_GEMINI_MODEL": "gemini-3.6-flash",
    "SHORTS_WHISPER_MODEL": "small",
    "SHORTS_API_HOST": "127.0.0.1",
    "SHORTS_API_PORT": "8100",
    "SHORTS_FFMPEG": "ffmpeg",
    "SHORTS_SUBTITLE_FONT": "Apple SD Gothic Neo",
    # 문서 §7 의 3.6 Flash 도입가. 🔴 가격은 자주 바뀌고 모델마다 다르다 — 화면에 "추정"으로
    # 표시하고 여기 값을 함께 보여준다. 2027-01-01 에 2배 인상 예정이라고 문서에 적혀 있다.
    "SHORTS_PRICE_INPUT_USD_PER_1M": "0.75",
    "SHORTS_PRICE_OUTPUT_USD_PER_1M": "3.75",
    "SHORTS_USD_KRW": "1400",
    "SHORTS_DB_PATH": "work/shorts.db",
    "SHORTS_WORK_DIR": "work",
    "SHORTS_SOURCE_DIR": "work/sources",
}


def _load_dotenv(path: Path) -> None:
    # 이미 셸에 있는 값이 우선이다 — .env 는 로컬 기본값일 뿐이라 CI/운영에서 덮어쓸 수 있어야 한다.
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _resolve(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str
    whisper_model: str
    api_host: str
    api_port: int
    api_token: str
    ffmpeg_bin: str
    subtitle_font: str
    price_input_usd_per_1m: float
    price_output_usd_per_1m: float
    usd_krw: float
    db_path: Path
    work_dir: Path
    source_dir: Path


def load() -> Config:
    _load_dotenv(REPO_ROOT / ".env")
    get = lambda k: os.environ.get(k) or DEFAULTS[k]  # noqa: E731
    return Config(
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        gemini_model=get("SHORTS_GEMINI_MODEL"),
        whisper_model=get("SHORTS_WHISPER_MODEL"),
        api_host=get("SHORTS_API_HOST"),
        api_port=int(get("SHORTS_API_PORT")),
        api_token=os.environ.get("SHORTS_API_TOKEN", ""),
        ffmpeg_bin=get("SHORTS_FFMPEG"),
        subtitle_font=get("SHORTS_SUBTITLE_FONT"),
        price_input_usd_per_1m=float(get("SHORTS_PRICE_INPUT_USD_PER_1M")),
        price_output_usd_per_1m=float(get("SHORTS_PRICE_OUTPUT_USD_PER_1M")),
        usd_krw=float(get("SHORTS_USD_KRW")),
        db_path=_resolve(get("SHORTS_DB_PATH")),
        work_dir=_resolve(get("SHORTS_WORK_DIR")),
        source_dir=_resolve(get("SHORTS_SOURCE_DIR")),
    )
