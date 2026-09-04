import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginPage } from "./pages/LoginPage";
import { ShortsPage } from "./pages/ShortsPage";

// 라우터를 쓰지 않는다. 화면이 로그인과 파이프라인 둘뿐이고 둘 사이 전환은 URL 이 아니라
// **인증 상태**가 결정한다 — react-router 를 넣으면 그 상태를 라우트로 한 번 더 표현해야 한다.
// 공유할 URL(클립 상세 같은 것)이 생기면 그때 넣는다.
function Gate() {
	const { status } = useAuth();

	// 첫 확인이 끝나기 전에 로그인 화면을 그리면 새로고침마다 화면이 번쩍인다.
	if (status === "checking") {
		return <p className="state">불러오는 중…</p>;
	}
	return status === "in" ? <ShortsPage /> : <LoginPage />;
}

export default function App() {
	return (
		<AuthProvider>
			<Gate />
		</AuthProvider>
	);
}
