# backend(FastAPI) 와 web(React) 을 **하나의 이미지**로 만든다.
#
# 컨테이너를 둘로 나누지 않는 이유: FastAPI 가 빌드된 정적 파일을 직접 서빙하므로 프론트와
# API 가 같은 오리진이 된다 — CORS 설정이 없고 세션 쿠키가 그냥 실린다. 서버 하나에서
# 도구 하나를 돌리는데 nginx 컨테이너를 더 두는 건 값을 못 한다.

# --- web: 프론트를 빌드한다 ---
FROM node:22-slim AS web

WORKDIR /web
# 의존성을 소스보다 먼저 넣어 레이어 캐시를 남긴다 — 화면만 고치면 재설치하지 않는다.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- build: 파이썬 의존성과 whisper 모델을 준비한다 ---
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_NO_CACHE=1 \
    HF_HOME=/opt/hf

# 🔴 --no-default-groups: dev 그룹(httpx)은 테스트용이라 이미지에 넣지 않는다.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --extra stt --no-install-project --no-default-groups

# 🔴 whisper 모델을 이미지에 구워둔다. 안 그러면 첫 STT 가 464MB 를 받느라 몇 분 멈추고,
# 그 사이 사용자는 잡이 멈춘 건지 다운로드 중인지 알 수 없다.
RUN uv run python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

COPY backend/src ./src
RUN uv sync --frozen --extra stt --no-default-groups

# --- runtime: uv 와 빌드 캐시를 남기지 않는다 ---
FROM python:3.12-slim AS runtime

# ffmpeg: 자막 번인에 libass 가 필요하다. 데비안 패키지에는 포함돼 있다
# (macOS Homebrew 기본 빌드에는 없어서 로컬에서는 ffmpeg-full 을 따로 깐다).
# 🔴 fonts-nanum: libass 는 폰트를 못 찾으면 **조용히 폴백해 두부(□)를 그린다.**
# slim 이미지에는 한글 폰트가 하나도 없어서 자막이 전부 □ 로 나온다(실제로 겪었다).
# fontconfig 는 fc-match 를 주며, 렌더 전에 폰트 존재를 확인하는 데 쓴다.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates fonts-nanum fontconfig \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

WORKDIR /app
# uv sync 는 프로젝트를 editable 로 깐다 — venv 만 옮기면 안 되고 소스도 같이 와야 한다.
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/hf /opt/hf
COPY --from=build /app/src /app/src
COPY --from=web /web/dist /app/web
# θ 튜닝용 질문 세트. `docker compose exec shorts sm answers eval-cluster` 가 읽는다 —
# CLI 를 컨테이너 안에서 쓰는 게 기본 경로라(README) 이미지에 있어야 한다. 몇 KB 다.
COPY backend/eval /app/eval

# 🔴 컨테이너에서는 호스트(nginx)가 붙어야 하므로 루프백으로는 안 된다.
# 루프백이 아닌 주소에 바인딩하려면 SHORTS_ADMIN_PASSWORD 가 있어야 기동된다(api.check_binding).
ENV PATH=/opt/venv/bin:$PATH \
    HF_HOME=/opt/hf \
    SHORTS_API_HOST=0.0.0.0 \
    SHORTS_API_PORT=8100 \
    SHORTS_DB_HOST=db \
    SHORTS_WORK_DIR=/data/work \
    SHORTS_SOURCE_DIR=/sources \
    SHORTS_WEB_DIR=/app/web \
    SHORTS_FFMPEG=ffmpeg \
    SHORTS_SUBTITLE_FONT=NanumGothic

EXPOSE 8100
CMD ["sm", "serve"]
