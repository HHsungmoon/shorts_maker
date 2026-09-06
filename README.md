# shorts_maker

긴 영상에서 단독으로 성립하는 숏폼 클립을 뽑아낸다. 1단계는 강연 영상.

설계 문서: [`docs/tease.md`](docs/tease.md)(제품·아키텍처·스키마) · [`docs/make_shorts.md`](docs/make_shorts.md)(파이프라인 내부·규약)

```
web/       React + Vite. 빌드 결과를 backend 가 같은 오리진에서 서빙한다
backend/   FastAPI + CLI. 파이프라인 본체
docs/      설계 문서
```

## 띄우기 — docker compose (기본)

로컬도 운영과 **같은 이미지**로 띄운다. 데비안 ffmpeg(libass 포함)·나눔 폰트·whisper 모델이 이미지에
들어 있어 맥에 따로 깔 게 없다. 서비스는 둘이다 — `db`(Postgres 17, 데이터는 `shorts-pg` 볼륨)와
`shorts`(앱, 청크·클립은 `shorts-data` 볼륨). 원본 영상은 `backend/sources/` 바인드 마운트.

```sh
brew install --cask docker           # Docker Desktop
cp backend/.env.example backend/.env # GEMINI_API_KEY, SHORTS_ADMIN_PASSWORD, POSTGRES_PASSWORD 를 채운다
docker compose up -d --build         # 첫 빌드는 몇 분 걸린다 (whisper 모델 464MB 포함)
open http://127.0.0.1:8100
```

🔴 `SHORTS_ADMIN_PASSWORD` 와 `POSTGRES_PASSWORD` 는 **필수**다. 앞은 컨테이너가 0.0.0.0 에 바인딩하므로
비어 있으면 기동을 거부하고, 뒤는 `db` 컨테이너가 계정을 만들 때 쓴다(`docker compose logs` 에 이유가 찍힌다).
스키마는 앱이 뜰 때 `db/migrations/` 를 순서대로 적용한다 — 따로 할 일이 없다.

```sh
docker compose logs -f shorts                 # 로그
docker compose exec shorts sm doctor          # 전제 확인 (컨테이너 안에서)
docker compose exec shorts sm db status       # 스키마 버전 · 테이블별 행 수
docker compose exec db psql -U shorts shorts  # SQL 직접
docker compose down -v                        # 🔴 볼륨까지 지운다 = DB·산출물 리셋 (원본 영상은 남는다)
```

CLI 는 전부 `docker compose exec shorts sm <명령>` 으로 쓴다. 원본 영상을 직접 넣을 때는
`backend/sources/` 에 파일을 두면 화면의 "새로 만들기 → 서버에 있는 영상" 에 바로 보인다.

화면을 고치는 중이라면 vite 개발 서버를 따로 띄우는 쪽이 빠르다(HMR). 백엔드는 컨테이너 그대로:

```sh
cd web && npm install && npm run dev        # http://localhost:5173
```

🔴 **반드시 5173 으로 접속한다.** vite 가 `/api`·`/auth` 를 8100 으로 프록시해 동일 오리진을
만들어 주므로 세션 쿠키가 실린다. 8100 을 직접 열면 이미지에 구워진 빌드를 보게 된다 — 화면
변경을 이미지에 반영하려면 `docker compose up -d --build`.

### 호스트에서 직접 띄우기 (테스트·디버깅용)

테스트와 CLI 는 uv 로 호스트에서 돌 수 있다. DB 는 컨테이너의 것을 그대로 쓴다 — `db` 서비스가
127.0.0.1:5432 를 열어 두고, `.env` 의 `SHORTS_DB_HOST=127.0.0.1` 이 거기를 가리킨다. 서버도 이렇게 띄울 수
있지만 자막 렌더에는 libass 포함 ffmpeg(`brew install ffmpeg-full`)와 한글 폰트가 따로 필요하다.

```sh
brew install uv ffmpeg node
docker compose up -d db                      # DB 만
cd backend && uv sync --extra stt
uv run sm doctor
uv run sm serve                              # 127.0.0.1:8100. 비밀번호가 비어 있으면 무인증 로컬 모드
```

호스트 실행과 컨테이너는 **같은 DB**(`db` 컨테이너의 `shorts`)를 본다. 산출물 폴더만 다르다 — 호스트는
`backend/work/`, 컨테이너는 `/data/work`. DB 에는 폴더 기준 상대경로만 들어가서 어느 쪽에서 만든 행이든
자기 폴더에 파일이 있으면 읽힌다.

## CLI

```sh
docker compose exec shorts sm db init
docker compose exec shorts sm source add <파일> --title T --origin URL --context "개요"
docker compose exec shorts sm chunk add 1 --start 1200 --end 1500     # 16kHz wav 로 추출
docker compose exec shorts sm stt run 1 --model small --prompt "칸트, 니체, 도덕법칙"
docker compose exec shorts sm segment run 1                           # [3] 주제 분할 (Gemini)
docker compose exec shorts sm rank run 1 --criteria "핵심 논지"       # [5] 선정 (Gemini)
docker compose exec shorts sm render 1                                # [7] 9:16 렌더 + 자막 번인
docker compose exec shorts sm render 1 --force --no-subtitles         # 자막 없이
```

`<파일>` 은 `backend/sources/` 기준 상대 경로다. 호스트 실행이면 `docker compose exec shorts` 를
`uv run` 으로 바꾸면 된다.

LLM 없이 손으로 구간을 지정하는 경로도 있다 — 직접 고를 때도 쓴다:

```sh
sm segment add 1 --from-utterance 0 --to-utterance 11 --summary "…"
sm run create 1
sm clip add --run 1 --segment 1 --from-utterance 3 --to-utterance 11
sm render 1
```

조회: `sm source list` · `sm chunk list` · `sm stt show 1` · `sm segment list 1` · `sm clip list` · `sm db status`

## 테스트

```sh
docker compose up -d db                                   # DB 테스트는 shorts_test 데이터베이스를 쓴다(자동 생성)
cd backend && uv run python -m unittest discover -s tests -t .
cd web && npm run build && npm run lint
```

whisper·Gemini 없이 도는 것만 테스트한다 — 모델 품질이 아니라 **시간 축 변환, 스키마 제약,
LLM 출력 검증, 인증 경계**가 대상이다. 이것들은 틀려도 결과물을 볼 때까지 아무도 모른다.

## 배포

Naver Cloud 단일 VM(4GB / 2vCPU) 에 `docker compose` 로 띄우고 nginx 가 앞에 선다.

```
인터넷 ──443──> nginx (VM) ──127.0.0.1:8100──> shorts 컨테이너
```

```sh
ssh root@<서버>
deploy-sm
```

[`scripts/deploy.sh`](scripts/deploy.sh) 가 `git pull` → `docker compose up -d --build`
→ 헬스체크까지 한다. nginx 설정 예시는 [`deploy/nginx-shorts.conf`](deploy/nginx-shorts.conf).

서버 최초 설정:

```sh
su - deploy -c 'git clone https://github.com/HHsungmoon/shorts_maker.git ~/shorts_maker'
su - deploy -c 'cp ~/shorts_maker/backend/deploy.env.example ~/shorts_maker/backend/.env'   # 값 채우기
printf '#!/bin/sh\nexec su - deploy -c /home/deploy/shorts_maker/scripts/deploy.sh\n' > /usr/local/bin/deploy-sm
chmod +x /usr/local/bin/deploy-sm
```

원본 영상을 직접 넣을 때는 `scp 강연.mp4 deploy@<서버>:~/shorts_maker/backend/sources/`.
(화면에서 유튜브 URL 로 등록하는 게 주 경로다.) 로컬과 서버가 같은 compose 파일을 쓴다 —
차이는 앞에 nginx 가 서느냐뿐이다.

## 알아둘 것

- 시간은 전부 **소스 절대 초**로 저장한다. 청크 로컬 시간이 아니다
- LLM 은 **인덱스만** 고르고 초는 `utterances` 에서 되찾는다 (설계 문서 §12)
- 잡은 워커 1개로 처리한다 — **동시 1건이 구조적으로 보장**된다. 2vCPU 에서는 이게 특히
  중요하다: STT 와 ffmpeg 가 겹치면 API 응답까지 같이 느려진다
- DB 의 파일 경로는 **상대경로**다 — 원본은 `backend/sources/`(컨테이너 `/sources`) 기준,
  파생물은 work 디렉터리(컨테이너 `/data/work`) 기준. 절대경로로 저장하던 시절 레포를 옮기자
  전 행이 깨졌다(2026-09-04)
- `backend/work/`·`backend/sources/*` 는 git 에서 제외된다 (원본 영상이 GB 단위)
