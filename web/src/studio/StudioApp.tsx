import { Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext";
import { LoginPage } from "./LoginPage";
import { SourcePage } from "./SourcePage";
import { StudioHome } from "./StudioHome";

// 스튜디오 안에도 라우트가 생겼다. 영상이 둘 이상이 되는 순간 "지금 작업 중인 영상"에 주소가
// 없다는 게 문제가 된다 — 링크로 줄 수도, 북마크할 수도, 새로고침해서 돌아올 수도 없다.
// URL 이 의미를 갖는 순간이 라우터가 필요해지는 순간이다(App.tsx 의 같은 판단).
//
// 로그인 화면은 여전히 라우트가 아니다. 그 전환은 URL 이 아니라 **인증 상태**가 결정한다 —
// 로그인 화면에 주소를 주면 공유·북마크가 가능해지는데 그건 의미가 없다.
function Gate() {
	const { status } = useAuth();

	// 첫 확인이 끝나기 전에 로그인 화면을 그리면 새로고침마다 화면이 번쩍인다.
	if (status === "checking") {
		return <p className="state">불러오는 중…</p>;
	}
	if (status !== "in") {
		return <LoginPage />;
	}
	// 🔴 App.tsx 가 이 트리를 `/*` 에 걸어 두므로 여기 경로는 상대경로다 — 앞에 `/` 를 붙이면
	// 어느 것도 매치되지 않는다. <Link to> 는 반대로 절대경로를 쓴다(`/sources/3`).
	return (
		<Routes>
			<Route index element={<StudioHome />} />
			<Route path="sources/:sourceId" element={<SourcePage />} />
			<Route path="*" element={<p className="state">없는 페이지입니다.</p>} />
		</Routes>
	);
}

export default function StudioApp() {
	return (
		<AuthProvider>
			<Gate />
		</AuthProvider>
	);
}
