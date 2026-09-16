"""환경설정 로딩. 의존성을 늘리지 않으려고 .env 파서를 직접 둔다(10줄이면 끝난다)."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

# 이 파일 기준 `backend/` 를 가리킨다. 상대 경로로 적힌 설정(.env·work·sources)은 전부
# 이 아래로 풀린다 — 레포 루트가 아니다. web/ 은 이 경로와 무관하다.
BACKEND_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS = {
    # DB 커넥션 풀의 최대 연결 수. 컨테이너는 compose 에서 20을 받고(2026-09-16), 이 기본값 8은
    # 호스트 CLI·테스트용이다 — 둘 다 프로세스 하나에서 순차로 일해 더 필요하지 않다.
    # 🔴 올릴 때 보는 값은 연결 수가 아니라 autovacuum·work_mem 이다(db/store.py 주석의 실측).
    "SHORTS_DB_POOL_MAX": "8",
    "SHORTS_GEMINI_MODEL": "gemini-3.6-flash",
    "SHORTS_WHISPER_MODEL": "small",
    # 임베딩. 🔴 dim 을 바꾸면 저장된 벡터와 섞이면 안 된다 — embeddings 테이블이 (model, dim, task_type)
    # 을 함께 저장하고 다른 조합은 무시한다. 768 을 쓰는 이유는 3072 대비 저장이 1/4 이고, 짧은 한국어
    # 질문끼리의 유사도 순위는 거의 같기 때문이다(θ 는 dim 에 따라 다시 튜닝해야 한다).
    "SHORTS_EMBED_MODEL": "gemini-embedding-001",
    "SHORTS_EMBED_DIM": "768",
    # 질문을 기존 대표 문장에 붙일 코사인 임계값. 넘지 못하면 단독 클러스터가 된다.
    # `sm answers eval-cluster` 로 튜닝한다 — tease §13 의 "0.85 시작".
    "SHORTS_CLUSTER_THETA": "0.85",
    # 답 클립의 길이 예산(초). 🔴 프롬프트에 알려주고 **코드가 강제한다** — LLM 이 말한 길이는 믿지 않는다.
    "SHORTS_TEASER_MAX_SEC": "30",
    # 🔴 청크 하나의 최대 길이(초). **메모리 상한이지 취향이 아니다** — whisper 는 전사 길이에 비례해
    # 메모리를 쓴다. 실측(2026-09-06): 30분 청크가 1.4GB, 95분을 한 번에 돌리자 컨테이너 한도
    # 2.93GB 를 넘겨 OOM 으로 죽었다. 25분이면 1.2GB 안쪽이라 4GB VM(운영)에서도 여유가 있다.
    # 사용자는 분석할 범위만 고르고, 그 안을 몇 조각으로 나눌지는 코드가 정한다.
    "SHORTS_CHUNK_MAX_SEC": "1800",
    # 질문으로 구간을 검색할 때 가져올 후보 수와, "이 영상엔 답이 없다" 로 볼 최소 유사도.
    # 🔴 프롬프트와 원본 응답을 stage_calls 에 남기는가. 판정이 이상할 때 **모델이 무엇을 보고
    # 무엇을 답했는지**를 화면에서 확인하는 유일한 길이다(마이그레이션 005). 끄면 그 확인을
    # 하려고 같은 호출을 다시 해야 하고, 무료 등급에서는 그 재현이 할당량을 깎는다.
    # 답하기가 낼 후보 수. 🔴 **하나 늘 때마다 judge 호출이 하나 는다** — 3이면 답변당 5회,
    # 5면 7회다(무료 등급은 하루 수십 회). 늘리면 고를 것이 많아지지만 크리에이터가 읽을 것도
    # 그만큼 늘어난다. 셋은 서로 뚜렷이 다른 방식(단일·조합·짧은 컷)이라 그 위는 변주에 가깝다.
    "SHORTS_ANSWER_CANDIDATES": "3",
    "SHORTS_STORE_PROMPTS": "1",
    "SHORTS_RETRIEVAL_TOP_K": "5",
    "SHORTS_RETRIEVAL_MIN_SIM": "0.5",
    # 질문을 남기는 순간 **발행된 숏폼을 추천**한다(answers/suggest.py, update_plan D13).
    # 🔴 공개 경로에서 외부 API 를 부르는 유일한 자리라 네 값이 전부 울타리다.
    # MIN_SIM: 질문(RETRIEVAL_QUERY) ↔ 클립 실제 대사(RETRIEVAL_DOCUMENT) 코사인. 실측(2026-09-15,
    #   질문 8 · 발행 숏폼 2): 맞는 연결 최저 0.735 · 붙으면 안 되는 것 최고 0.720 → 가운데 0.73.
    #   🔴 표본이 작다. 평가 세트로 다시 정한다. 제목이나 구간 설명과 비교하면 틈이 음수였다.
    # MAX: 한 번에 보여줄 숏폼 수.
    # PER_MIN: 공개 경로 전체의 분당 임베딩 상한. 크리에이터 작업과 분당 한도(100)를 나눠 쓰므로
    #   넘으면 추천만 건너뛴다. **0 이면 기능이 꺼지고 공개 경로 외부 호출은 다시 0회가 된다.**
    # TIMEOUT_SEC: 재시도 없이 이만큼만 기다린다. 시청자의 질문 등록이 이 이상 멈추지 않는다.
    "SHORTS_SUGGEST_MIN_SIM": "0.73",
    "SHORTS_SUGGEST_MAX": "2",
    "SHORTS_SUGGEST_PER_MIN": "30",
    "SHORTS_SUGGEST_TIMEOUT_SEC": "3",
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
    embed_model: str
    embed_dim: int
    cluster_theta: float
    teaser_max_sec: float
    chunk_max_sec: float
    answer_candidates: int
    store_prompts: bool
    retrieval_top_k: int
    retrieval_min_sim: float
    suggest_min_sim: float
    suggest_max: int
    suggest_per_min: int
    suggest_timeout_sec: float
    api_host: str
    api_port: int
    api_token: str
    admin_password: str
    readonly_password: str
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
    # 🔴 기본값이 있는 필드라 맨 뒤다(dataclass 규칙). 서버만 이 값을 쓴다 — CLI·테스트는 기본 8.
    db_pool_max: int = 8

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
        embed_model=get("SHORTS_EMBED_MODEL"),
        embed_dim=int(get("SHORTS_EMBED_DIM")),
        cluster_theta=float(get("SHORTS_CLUSTER_THETA")),
        teaser_max_sec=float(get("SHORTS_TEASER_MAX_SEC")),
        chunk_max_sec=float(get("SHORTS_CHUNK_MAX_SEC")),
        answer_candidates=max(1, int(get("SHORTS_ANSWER_CANDIDATES"))),
        store_prompts=get("SHORTS_STORE_PROMPTS") not in ("0", "false", "False", ""),
        retrieval_top_k=int(get("SHORTS_RETRIEVAL_TOP_K")),
        retrieval_min_sim=float(get("SHORTS_RETRIEVAL_MIN_SIM")),
        suggest_min_sim=float(get("SHORTS_SUGGEST_MIN_SIM")),
        suggest_max=max(0, int(get("SHORTS_SUGGEST_MAX"))),
        suggest_per_min=max(0, int(get("SHORTS_SUGGEST_PER_MIN"))),
        suggest_timeout_sec=max(0.5, float(get("SHORTS_SUGGEST_TIMEOUT_SEC"))),
        api_host=api_host,
        api_port=int(get("SHORTS_API_PORT")),
        api_token=os.environ.get("SHORTS_API_TOKEN", ""),
        admin_password=os.environ.get("SHORTS_ADMIN_PASSWORD", ""),
        readonly_password=os.environ.get("SHORTS_READONLY_PASSWORD", ""),
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
        db_pool_max=max(1, int(get("SHORTS_DB_POOL_MAX"))),
        work_dir=_resolve(get("SHORTS_WORK_DIR")),
        source_dir=_resolve(get("SHORTS_SOURCE_DIR")),
    )
