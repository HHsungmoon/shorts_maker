# shorts_maker

긴 영상에서 단독으로 성립하는 숏폼 클립을 뽑아낸다. 1단계는 강연 영상.

설계 문서: [`docs/tease.md`](docs/tease.md)(제품·아키텍처·스키마) · [`docs/make_shorts.md`](docs/make_shorts.md)(파이프라인 내부·규약)

```
web/       React + Vite. 빌드 결과를 backend 가 같은 오리진에서 서빙한다
backend/   FastAPI + CLI. 파이프라인 본체
docs/      설계 문서
```

## 준비

```sh
brew install uv ffmpeg node
cd backend && uv sync --extra stt
cp .env.example .env         # GEMINI_API_KEY 를 채운다
uv run sm doctor             # 전제 확인 — 전부 ok 여야 다음 단계로 간다
```

## 띄우기

```sh
cd backend && uv run sm serve        # 127.0.0.1:8100
```

`http://127.0.0.1:8100` 을 연다. `web/` 을 빌드해두면 그 화면이 뜨고, 안 했으면
개발용 단일 페이지로 폴백한다(API 확인용).

```sh
cd web && npm install && npm run build   # 한 번 해두면 backend 가 서빙한다
```

화면을 고치는 중이라면 vite 개발 서버를 따로 띄우는 쪽이 빠르다(HMR):

```sh
cd web && npm run dev                # http://localhost:5173
```

🔴 **반드시 5173 으로 접속한다.** vite 가 `/api`·`/auth` 를 8100 으로 프록시해 동일
오리진을 만들어 주므로 세션 쿠키가 실린다. 8100 을 직접 열면 옛 빌드 결과를 보게 된다.

### 로그인

`SHORTS_ADMIN_PASSWORD` 가 **비어 있으면 로그인 화면이 뜨지 않는다** — 루프백 전용
로컬 개발 모드다. 로그인 화면을 보려면 값을 넣고 다시 띄운다:

```sh
SHORTS_ADMIN_PASSWORD=dev1234 uv run sm serve
```

🔴 루프백이 아닌 주소(`SHORTS_API_HOST=0.0.0.0`)에 바인딩하려면 이 값이 **반드시**
있어야 하고, 없으면 기동을 거부한다. 무인증 인스턴스가 외부에 열리는 경로를 코드가 막는다.

## CLI

화면 없이 돌릴 때. 인증과 무관하게 동작한다(같은 DB 를 직접 연다).

```sh
cd backend
uv run sm db init
uv run sm source add <파일> --title T --origin URL --context "개요"
uv run sm chunk add 1 --start 1200 --end 1500          # 16kHz wav 로 추출
uv run sm stt run 1 --model small --prompt "칸트, 니체, 도덕법칙"
uv run sm segment run 1                                 # [3] 주제 분할 (Gemini)
uv run sm rank run 1 --criteria "핵심 논지"             # [5] 선정 (Gemini)
uv run sm render 1                                      # [7] 9:16 렌더 + 자막 번인
uv run sm render 1 --force --no-subtitles                # 자막 없이
```

LLM 없이 손으로 구간을 지정하는 경로도 있다 — 직접 고를 때도 쓴다:

```sh
uv run sm segment add 1 --from-utterance 0 --to-utterance 11 --summary "…"
uv run sm run create 1
uv run sm clip add --run 1 --segment 1 --from-utterance 3 --to-utterance 11
uv run sm render 1
```

조회: `sm source list` · `sm chunk list` · `sm stt show 1` · `sm segment list 1` · `sm clip list` · `sm db status`
초기화: `sm db reset --yes`

## 테스트

```sh
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
(화면에서 유튜브 URL 로 등록하는 게 주 경로다.)

## 알아둘 것

- 시간은 전부 **소스 절대 초**로 저장한다. 청크 로컬 시간이 아니다
- LLM 은 **인덱스만** 고르고 초는 `utterances` 에서 되찾는다 (설계 문서 §12)
- 잡은 워커 1개로 처리한다 — **동시 1건이 구조적으로 보장**된다. 2vCPU 에서는 이게 특히
  중요하다: STT 와 ffmpeg 가 겹치면 API 응답까지 같이 느려진다
- `backend/work/` 는 git 에서 제외된다 (원본 영상이 GB 단위)
