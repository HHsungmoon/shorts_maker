"""환경설정 로딩. 의존성을 늘리지 않으려고 .env 파서를 직접 둔다(10줄이면 끝난다)."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

# 이 파일 기준 `backend/` 를 가리킨다. 상대 경로로 적힌 설정(.env·work·sources)은 전부
# 이 아래로 풀린다 — 레포 루트가 아니다. web/ 은 이 경로와 무관하다.
BACKEND_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS = {
    "SHORTS_GEMINI_MODEL": "gemini-3.6-flash",
    "SHORTS_WHISPER_MODEL": "small",
    "SHORTS_API_HOST": "127.0.0.1",
    "SHORTS_API_PORT": "8100",
    "SHORTS_FFMPEG": "ffmpeg",
    "SHORTS_SUBTITLE_FONT": "Apple SD Gothic Neo",
    # Gemini 3.6 Flash 유료 등급. 공식 가격 페이지에서 확인함(2026-08-31):
    #   2026-12-31 까지 $0.75 / $3.75 → 2027-01-01 부터 $1.50 / $7.50 (2배)
    # 🔴 **무료 등급이면 실제 청구는 0 이다.** 그 경우 화면 숫자는 "유료였다면" 값이다.
    # Batch API 는 50% 라 쓰게 되면 여기도 바꿔야 한다.
    "SHORTS_PRICE_INPUT_USD_PER_1M": "0.75",
    "SHORTS_PRICE_OUTPUT_USD_PER_1M": "3.75",
    "SHORTS_USD_KRW": "1400",
    # Postgres 접속. 조각으로 받는 이유: compose 가 호스트(db)만 덮고 비밀번호는 db 컨테이너와
    # **같은 변수**(POSTGRES_PASSWORD, 공식 이미지가 읽는 이름)를 env_file 로 나눠 갖기 위해서다.
    # SHORTS_DATABASE_URL 을 주면 조각은 무시하고 그걸 쓴다(관리형 DB 등).
    "SHORTS_DB_HOST": "127.0.0.1",
    "SHORTS_DB_PORT": "5432",
    "SHORTS_DB_NAME": "shorts",
    "SHORTS_DB_USER": "shorts",
    "SHORTS_WORK_DIR": "work",
    # compose 가 `./backend/sources` 를 `/sources` 로 마운트한다. 호스트에서 uv 로 직접 띄워도
    # 같은 디렉터리를 보게 기본값을 맞춘다 — 두 실행 방식이 다른 폴더를 보면 "등록했는데 없다"가 된다.
    "SHORTS_SOURCE_DIR": "sources",
    # 세션 수명. 하루 작업을 한 번의 로그인으로 끝내되, 자리를 뜬 브라우저가 무기한
    # 열려 있지는 않을 만큼으로 잡았다.
    "SHORTS_SESSION_TTL_HOURS": "12",
    # 빌드된 프론트(web/dist). 없으면 개발용 단일 페이지로 폴백한다 — node 빌드 없이도
    # 브라우저로 파이프라인 상태를 볼 수 있어야 디버깅이 된다.
    "SHORTS_WEB_DIR": "../web/dist",
}

# 이 주소에 바인딩하면 외부에서 닿지 않는다 — 인증 없이 띄워도 되는 유일한 경우다.
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


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
    return p if p.is_absolute() else BACKEND_ROOT / p


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str
    whisper_model: str
    api_host: str
    api_port: int
    api_token: str
    admin_password: str
    session_ttl_hours: int
    cookie_secure: bool
    web_dir: Path
    ffmpeg_bin: str
    subtitle_font: str
    price_input_usd_per_1m: float
    price_output_usd_per_1m: float
    usd_krw: float
    # postgresql://user:pass@host:port/dbname. 🔴 로그에 찍지 않는다 — 비밀번호가 들어 있다.
    database_url: str
    work_dir: Path
    source_dir: Path

    # ---- DB 에 저장되는 파일 경로 ------------------------------------------------
    # 🔴 DB 에는 **상대경로**를 넣는다. 원본은 source_dir 기준, 파생물(청크·클립)은 work_dir 기준.
    # 절대경로를 넣었다가 레포를 옮기자 다섯 행이 전부 깨졌고(2026-09-04), 호스트(`~/dev/...`)와
    # 컨테이너(`/sources`, `/data/work`)는 애초에 경로가 다르다. 상대경로면 DB 파일을 어디로
    # 들고 가도 그대로 읽힌다. 절대경로가 들어 있으면 그대로 쓴다 — 옛 DB 를 위한 폴백이다.

    def source_file(self, stored: str) -> Path:
        p = Path(stored)
        return p if p.is_absolute() else self.source_dir / p

    def work_file(self, stored: str) -> Path:
        p = Path(stored)
        return p if p.is_absolute() else self.work_dir / p

    def store_source(self, path: Path) -> str:
        return _relative_or_absolute(path, self.source_dir)

    def store_work(self, path: Path) -> str:
        return _relative_or_absolute(path, self.work_dir)


def _relative_or_absolute(path: Path, root: Path) -> str:
    """root 아래면 posix 상대경로, 아니면 절대경로 문자열.

    root 밖 파일은 원래 가드(ingest.resolve_source_path·media.resolve)가 막으니 여기 오면
    안 되지만, 왔을 때 조용히 잘못된 상대경로를 만드는 것보다 절대경로로 남기는 편이 낫다.
    """
    resolved = path.resolve()
    root = root.resolve()
    if resolved.is_relative_to(root):
        return resolved.relative_to(root).as_posix()
    return str(resolved)


def database_url_from_env(get) -> str:
    explicit = os.environ.get("SHORTS_DATABASE_URL")
    if explicit:
        return explicit
    password = os.environ.get("POSTGRES_PASSWORD", "")
    auth = quote(get("SHORTS_DB_USER"), safe="")
    if password:
        auth += ":" + quote(password, safe="")
    return f"postgresql://{auth}@{get('SHORTS_DB_HOST')}:{get('SHORTS_DB_PORT')}/{get('SHORTS_DB_NAME')}"


def load() -> Config:
    _load_dotenv(BACKEND_ROOT / ".env")
    get = lambda k: os.environ.get(k) or DEFAULTS[k]  # noqa: E731
    api_host = get("SHORTS_API_HOST")
    # 🔴 Secure 쿠키는 https 에서만 저장된다. 로컬(http://127.0.0.1)에 켜면 브라우저가
    # 쿠키를 조용히 버려서 "로그인은 되는데 계속 로그인 화면"이 된다. 그래서 기본값을
    # 바인딩 주소에서 유도한다 — 루프백이면 끄고, 외부에 열면(=nginx+TLS 뒤) 켠다.
    cookie_secure = os.environ.get("SHORTS_COOKIE_SECURE")
    return Config(
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        gemini_model=get("SHORTS_GEMINI_MODEL"),
        whisper_model=get("SHORTS_WHISPER_MODEL"),
        api_host=api_host,
        api_port=int(get("SHORTS_API_PORT")),
        api_token=os.environ.get("SHORTS_API_TOKEN", ""),
        admin_password=os.environ.get("SHORTS_ADMIN_PASSWORD", ""),
        session_ttl_hours=int(get("SHORTS_SESSION_TTL_HOURS")),
        cookie_secure=(
            cookie_secure.strip().lower() in ("1", "true", "yes")
            if cookie_secure
            else api_host not in LOOPBACK
        ),
        web_dir=_resolve(get("SHORTS_WEB_DIR")),
        ffmpeg_bin=get("SHORTS_FFMPEG"),
        subtitle_font=get("SHORTS_SUBTITLE_FONT"),
        price_input_usd_per_1m=float(get("SHORTS_PRICE_INPUT_USD_PER_1M")),
        price_output_usd_per_1m=float(get("SHORTS_PRICE_OUTPUT_USD_PER_1M")),
        usd_krw=float(get("SHORTS_USD_KRW")),
        database_url=database_url_from_env(get),
        work_dir=_resolve(get("SHORTS_WORK_DIR")),
        source_dir=_resolve(get("SHORTS_SOURCE_DIR")),
    )
