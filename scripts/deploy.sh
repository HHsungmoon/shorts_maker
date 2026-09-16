#!/usr/bin/env bash
# 서버에서 직접 실행하는 배포 스크립트.
#
#   ssh root@<서버>
#   deploy-sm
#
# 🔴 **전체가 main() 안에 있고 마지막 줄에서 부른다. 이 구조를 풀지 마라.**
# 이 스크립트는 `git pull` 로 **자기 자신을 갈아치운다.** bash 는 스크립트를 한 번에 읽지 않고
# 실행하면서 조금씩 읽어 들이기 때문에, 함수로 감싸지 않으면 pull 로 파일 길이가 바뀌는 순간
# 바뀐 파일의 엉뚱한 오프셋부터 이어 읽어 문법 오류를 내거나 명령을 반쯤 건너뛴다.
# main 을 부르는 시점엔 이미 파일 끝까지 읽은 뒤라 그 뒤에 파일이 바뀌어도 안전하다.
set -euo pipefail

# 롤백 지점. compose.yaml 이 `image: shorts-maker:latest` 로 태그를 고정하기 때문에
# 이 이름이 폴더 이름과 무관하게 일정하다.
IMAGE=shorts-maker:latest
PREV=shorts-maker:prev

main() {
	cd "$(dirname "$0")/.."

	# `.env` 는 gitignore 되어 있어 git pull 이 건드리지 않는다. 없으면 기동 자체가 거부되므로
	# 컨테이너를 내리기 전에 먼저 막는다.
	if [ ! -f backend/.env ]; then
		echo "error: backend/.env is missing — see backend/deploy.env.example" >&2
		exit 1
	fi

	# 🔴 컨테이너는 0.0.0.0 에 바인딩한다. 비밀번호가 없으면 무인증 인스턴스가 되므로 앱이
	# 기동을 거부하는데, 그 실패를 배포 로그 한복판에서 보는 것보다 여기서 먼저 알려준다.
	if ! grep -q '^SHORTS_ADMIN_PASSWORD=.\+' backend/.env; then
		echo "error: SHORTS_ADMIN_PASSWORD is empty in backend/.env — the service refuses to start without it" >&2
		echo "       openssl rand -base64 24" >&2
		exit 1
	fi

	BRANCH=$(git rev-parse --abbrev-ref HEAD)
	echo "==> deploying branch: $BRANCH"
	if [ "$BRANCH" != "main" ]; then
		echo "warning: not on main" >&2
	fi

	# 빌드 전에 지금 돌고 있는 이미지의 ID 를 붙든다. 빌드가 같은 태그를 덮어쓰면 이 ID 는
	# 태그를 잃으므로(<none>), 여기서 기억해 두지 않으면 되돌아갈 곳이 사라진다.
	PREV_ID=$(docker image inspect -f '{{.Id}}' "$IMAGE" 2>/dev/null || true)

	echo "==> pulling"
	git pull --ff-only

	echo "==> building and restarting"
	docker compose up -d --build

	# 🔴 롤백 지점은 **헬스체크보다 먼저** 만든다. 되돌릴 일이 생기는 건 헬스체크가 실패했을
	# 때인데, 실패하면 아래에서 exit 1 로 빠져나가 여기까지 오지 못하기 때문이다.
	#
	# 이미지를 딱 두 벌로 유지하는 방법이기도 하다. `docker image prune` 은 **태그 없는 것만**
	# 지우므로, 직전 것 하나에 태그를 달아 두면 그것만 살아남고 더 오래된 것들은 태그를 잃은
	# 채 prune 에 쓸려 간다. 새 prev 를 달면 옛 prev 도 태그를 잃어 다음 배포 때 정리된다.
	# 이 이미지는 whisper 모델 464MB 를 품어 한 벌이 수 GB다 — 50GB 디스크에서 무한정 쌓게 둘 수 없다.
	NEW_ID=$(docker image inspect -f '{{.Id}}' "$IMAGE" 2>/dev/null || true)
	if [ -n "$PREV_ID" ] && [ "$PREV_ID" != "$NEW_ID" ]; then
		docker tag "$PREV_ID" "$PREV"
		echo "==> rollback point: $PREV"
	fi

	echo "==> waiting for the service to answer"
	for i in $(seq 1 30); do
		if docker compose exec -T shorts python -c \
			"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/health')" > /dev/null 2>&1; then
			echo "shorts_maker is up"
			break
		fi
		if [ "$i" -eq 30 ]; then
			echo "error: service did not become healthy within 150s" >&2
			docker compose logs --tail 50 shorts >&2
			if docker image inspect "$PREV" > /dev/null 2>&1; then
				echo "" >&2
				echo "to roll back:" >&2
				echo "  docker tag $PREV $IMAGE && docker compose up -d" >&2
			fi
			exit 1
		fi
		sleep 5
	done

	# 태그를 잃은 옛 이미지와 빌드 캐시를 정리한다. 🔴 prune 은 태그 없는 것만 가져가므로
	# 위에서 태그를 단 $PREV 와 현재 $IMAGE 는 안전하다.
	# 빌드 캐시는 72h 필터를 두어 연속 배포에서는 캐시가 살아 있게 한다 — 이걸 다 지우면
	# 다음 배포가 npm ci 와 uv sync 를 처음부터 다시 한다(2vCPU 에서 10분 이상).
	# 11번 문서 실측으로 이 캐시는 1.7GB 까지 자란다. 주간 cron 이 백스톱이다.
	echo "==> pruning"
	docker image prune -f > /dev/null
	docker builder prune -f --filter until=72h > /dev/null
	docker image ls "shorts-maker" --format '    {{.Repository}}:{{.Tag}}  {{.Size}}  {{.CreatedSince}}'
}

main "$@"
