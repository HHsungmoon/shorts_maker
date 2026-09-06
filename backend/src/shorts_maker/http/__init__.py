"""HTTP 층 — FastAPI 앱 조립과 라우터. 도메인 로직은 없다.

server.py      앱 조립 · 공개 라우트(/auth/** · /health · /debug · SPA catch-all) · serve()
deps.py        cfg · 잡 큐 · require_auth · DB 접속 — 라우터들이 공유
studio.py      크리에이터용 /api/** (라우터 레벨 인증)
watch.py       (M3) 시청자용 /api/watch/** — 별도 라우터, 인증 없음, 발행된 것만
auth.py        비밀번호 + HMAC 세션 쿠키. stdlib
debug_page.py  node 빌드 없이 보는 개발용 단일 페이지
"""
