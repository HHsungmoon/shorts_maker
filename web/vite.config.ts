import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발 중에는 이 프록시를 거쳐 백엔드를 부른다. 브라우저 입장에서 동일 오리진이 되므로
// 🔴 **세션 쿠키가 그대로 실린다** — 프록시 없이 :5173 에서 :8100 을 직접 부르면
// 교차 오리진이라 쿠키가 빠지고 로그인이 통째로 동작하지 않는다.
//
// 배포 빌드는 FastAPI 가 이 dist 를 같은 오리진에서 서빙하므로 프록시도 CORS 도 필요 없다.
export default defineConfig({
	plugins: [react()],
	build: { outDir: "dist" },
	server: {
		proxy: Object.fromEntries(
			["/api", "/auth", "/health"].map((path) => [
				path,
				{ target: process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8100" },
			]),
		),
	},
});
