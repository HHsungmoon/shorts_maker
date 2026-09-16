import { Suspense, lazy } from "react";
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

const StudioApp = lazy(() => import("./studio/StudioApp"));
const WatchApp = lazy(() => import("./watch/WatchApp"));

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
