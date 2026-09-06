import { Suspense, lazy } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

// 트리가 둘이다. 크리에이터가 쓰는 스튜디오(`/`)와 시청자가 쓰는 공개 화면(`/watch`).
//
// 🔴 **lazy 로 갈라야 하는 이유는 번들이 아니라 인증이다.** 스튜디오 트리는 마운트되면서
// `/auth/me` 를 부르고 로그인 화면으로 전환한다. 한 트리로 두면 시청자도 그 요청을 보내고,
// 로그인하지 않은 사람에게 로그인 화면이 번쩍인다. 라우트로 갈라 두면 시청자는 스튜디오
// 코드를 내려받지도, 인증 요청을 보내지도 않는다.
//
// 라우터를 쓰는 이유(예전엔 인증 상태만으로 전환했다): 시청자가 영상 페이지 링크를 공유해야
// 하고 뒤로가기가 동작해야 한다. URL 이 의미를 갖는 순간이 라우터가 필요해지는 순간이다.
const StudioApp = lazy(() => import("./studio/StudioApp"));
const WatchApp = lazy(() => import("./watch/WatchApp"));

export default function App() {
	return (
		<BrowserRouter>
			<Suspense fallback={<p className="state">불러오는 중…</p>}>
				<Routes>
					<Route path="/watch/*" element={<WatchApp />} />
					{/* 나머지는 전부 스튜디오. 서버의 catch-all 이 어느 경로든 index.html 을 주므로
					    새로고침해도 여기로 돌아온다(http/server.py 의 spa). */}
					<Route path="/*" element={<StudioApp />} />
				</Routes>
			</Suspense>
		</BrowserRouter>
	);
}
