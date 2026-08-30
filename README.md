# shorts_maker

긴 영상에서 단독으로 성립하는 숏폼 클립을 뽑아낸다. 1단계는 강연 영상.

설계 문서: `../backend/docs/make_shorts.md` — **전제가 바뀌면 그 문서의 §1 부터 고친다.**

## 준비

```sh
brew install uv ffmpeg
uv sync --extra stt
cp .env.example .env         # GEMINI_API_KEY 를 채운다
uv run sm doctor             # 전제 확인 — 전부 ok 여야 다음 단계로 간다
```

## 관리자 페이지에서 쓰기

```sh
uv run sm serve              # 127.0.0.1:8100
```

그 다음 backend(`./gradlew bootRun`)와 admin-web(`npm run dev`)을 띄우고
관리자 페이지의 **숏폼 (베타)** 탭으로 들어간다. backend 가 프록시하므로
shorts_maker 를 외부에 노출하지 않는다.

`http://127.0.0.1:8100/` 에도 같은 내용을 보는 로컬 확인용 화면이 있다(디버깅용).

## CLI

```sh
uv run sm db init
uv run sm source add <파일> --title T --origin URL --context "개요"
uv run sm chunk add 1 --start 1200 --end 1500          # 16kHz wav 로 추출
uv run sm stt run 1 --model small --prompt "칸트, 니체, 도덕법칙"
uv run sm segment run 1                                 # [3] 주제 분할 (Gemini)
uv run sm rank run 1 --criteria "핵심 논지"             # [5] 선정 (Gemini)
uv run sm render 1                                      # [7] 9:16 렌더 + 자막 번인
uv run sm render 1 --force --no-subtitles                # 자막 없이
```

LLM 없이 손으로 구간을 지정하는 경로도 있다 — 관리자가 직접 고를 때도 쓴다:

```sh
uv run sm segment add 1 --from-utterance 0 --to-utterance 11 --summary "…"
uv run sm run create 1
uv run sm clip add --run 1 --segment 1 --from-utterance 3 --to-utterance 11
uv run sm render 1
```

조회: `sm source list` · `sm chunk list` · `sm stt show 1` · `sm segment list 1` · `sm clip list` · `sm db status`
초기화: `sm db reset --yes` (A 단계에서는 스키마가 자주 바뀐다)

## 테스트

```sh
uv run python -m unittest discover -s tests -t .
```

whisper·Gemini 없이 도는 것만 테스트한다 — 모델 품질이 아니라 **시간 축 변환, 스키마 제약,
LLM 출력 검증**이 대상이다. 이 셋은 틀려도 결과물을 볼 때까지 아무도 모른다.

## 알아둘 것

- 시간은 전부 **소스 절대 초**로 저장한다. 청크 로컬 시간이 아니다
- LLM 은 **인덱스만** 고르고 초는 `utterances` 에서 되찾는다 (설계 문서 §12)
- 잡은 워커 1개로 처리한다 — **동시 1건이 구조적으로 보장**된다
- `work/` 는 git 에서 제외된다 (원본 영상이 GB 단위)
