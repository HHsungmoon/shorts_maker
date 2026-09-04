#!/usr/bin/env bash
# 서버에서 직접 실행하는 배포 스크립트.
#
#   ssh root@<서버>
#   deploy-sm
set -euo pipefail

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
		exit 1
	fi
	sleep 5
done

echo "==> pruning dangling images"
docker image prune -f > /dev/null
