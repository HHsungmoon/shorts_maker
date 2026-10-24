import { Suspense, lazy } from "react";
import type { ComponentType } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

// 트리가 셋이다. 첫 화면(`/`)·시청자(`/watch`)·스튜디오(`/studio`).
//
// 🔴 **lazy 로 갈라야 하는 이유는 번들이 아니라 인증이다.** 스튜디오 트리는 마운트되면서
// `/auth/me` 를 부르고 로그인 화면으로 전환한다. 한 트리로 두면 시청자도 그 요청을 보내고,
// 로그인하지 않은 사람에게 로그인 화면이 번쩍인다. 라우트로 갈라 두면 시청자는 스튜디오
// 코드를 내려받지도, 인증 요청을 보내지도 않는다.
//
// 라우터를 쓰는 이유(예전엔 인증 상태만으로 전환했다): 시청자가 영상 페이지 링크를 공유해야
// 하고 뒤로가기가 동작해야 한다. URL 이 의미를 갖는 순간이 라우터가 필요해지는 순간이다.
import { LandingPage } from "./landing/LandingPage";

/**
 * 조각을 못 받으면 페이지를 한 번 새로 불러온다.
 *
 * 🔴 재배포 전에 열어 둔 탭은 옛 index.html 이 적어 둔 **옛 해시의 조각 이름**을 기억한다. 그 탭에서
 * 첫 화면 → 시청자 화면으로 넘어가면 이미 지워진 조각을 부르고, 실패한 lazy 는 화면을 하얗게 남겼다
 * — 새로고침해야만 나왔다(2026-10-24). 새로 불러오면 새 index.html 이 새 조각 이름을 준다.
 * 🔴 한 번만이다. 조각이 정말로 깨졌으면 무한 새로고침이 되므로, 이 탭에서 이미 한 번 했으면
 * 오류를 그대로 던진다(sessionStorage 표시는 성공하면 지운다).
 */
const RELOADED = "clipq-chunk-reload";

function lazyPage(load: () => Promise<{ default: ComponentType }>) {
	return lazy(() =>
		load().then(
			(module) => {
				try {
					sessionStorage.removeItem(RELOADED);
				} catch {
					// 저장소가 막힌 브라우저. 표시가 없으니 지울 것도 없다.
				}
				return module;
			},
			(error: unknown) => {
				let already = false;
				try {
					already = sessionStorage.getItem(RELOADED) !== null;
					sessionStorage.setItem(RELOADED, "1");
				} catch {
					// 저장소가 막히면 한 번만을 보장할 수 없다 — 새로고침하지 않고 오류를 낸다.
					already = true;
				}
				if (already) {
					throw error;
				}
				window.location.reload();
				// 새로고침이 끝날 때까지 "불러오는 중…" 에 머문다.
				return new Promise<never>(() => {});
			},
		),
	);
}

const StudioApp = lazyPage(() => import("./studio/StudioApp"));
const WatchApp = lazyPage(() => import("./watch/WatchApp"));

export default function App() {
	return (
		<BrowserRouter>
			<Suspense fallback={<p className="state">불러오는 중…</p>}>
				<Routes>
					{/* 🔴 `/` 가 랜딩이 됐다(2026-09-16). 예전에는 스튜디오가 `/*` 를 통째로 가져가서
					    도메인을 그냥 누른 사람이 **관리자 로그인 화면**을 먼저 만났다 — 수가 많은 쪽은
					    시청자인데 첫 화면이 남의 도구였다. */}
					<Route path="/" element={<LandingPage />} />
					<Route path="/watch/*" element={<WatchApp />} />
					<Route path="/studio/*" element={<StudioApp />} />
					{/* 서버의 catch-all 이 어느 경로든 index.html 을 주므로(http/server.py 의 spa)
					    오타 주소도 여기까지 온다. 첫 화면으로 돌려보낸다. */}
					<Route path="*" element={<LandingPage />} />
				</Routes>
			</Suspense>
		</BrowserRouter>
	);
}
