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

	# 🔴 롤백 지점은 **빌드 전에, 태그로** 만든다(2026-09-17에 고쳤다).
	#
	# 예전에는 빌드 전에 이미지 **ID** 만 붙들었다가 빌드가 끝난 뒤 그 ID 에 태그를 달았다.
	# 실제 배포에서 `Error response from daemon: No such image` 로 죽었다 — 새 빌드가 같은 태그를
	# 가져가면 옛 이미지는 참조가 하나도 없어져 그 자리에서 회수된다(containerd 이미지 저장소).
	# 즉 "빌드 뒤에 옛 ID 가 남아 있다" 는 전제가 틀렸다. `set -e` 라 그 한 줄이 배포 전체를 끊어
	# **헬스체크도 prune 도 돌지 않았다** — 컨테이너는 떠 있는데 스크립트만 죽어 조용했다.
	#
	# 이미지가 살아 있을 때 태그를 달아 두는 것이 유일하게 확실하다. 실패해도 배포는 계속한다:
	# 롤백 지점이 없는 것은 불편이고, 배포가 중간에 멈추는 것은 장애다.
	#
	# 이미지를 딱 두 벌로 유지하는 방법이기도 하다. `docker image prune` 은 **태그 없는 것만**
	# 지우므로, 직전 것 하나에 태그를 달아 두면 그것만 살아남고 더 오래된 것들은 태그를 잃은
	# 채 prune 에 쓸려 간다. 이 이미지는 whisper 모델 464MB 를 품어 한 벌이 수 GB다.
	if docker image inspect "$IMAGE" > /dev/null 2>&1; then
		if docker tag "$IMAGE" "$PREV" 2>/dev/null; then
			echo "==> rollback point: $PREV"
		else
			echo "warning: could not tag the rollback point — deploying anyway" >&2
		fi
	fi

	echo "==> pulling"
	git pull --ff-only

	echo "==> building and restarting"
	docker compose up -d --build

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
