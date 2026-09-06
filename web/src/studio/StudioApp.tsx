import { AuthProvider, useAuth } from "./AuthContext";
import { LoginPage } from "./LoginPage";
import { ShortsPage } from "./ShortsPage";

// 스튜디오 안에는 라우트를 두지 않는다. 화면이 로그인과 파이프라인 둘뿐이고 그 전환은 URL 이
// 아니라 **인증 상태**가 결정한다 — 로그인 화면에 URL 을 주면 공유·북마크가 가능해지는데
// 그건 의미가 없다. 클러스터 패널·인사이트처럼 공유할 화면이 생기면 그때 여기에 라우트를 넣는다.
function Gate() {
	const { status } = useAuth();

	// 첫 확인이 끝나기 전에 로그인 화면을 그리면 새로고침마다 화면이 번쩍인다.
	if (status === "checking") {
		return <p className="state">불러오는 중…</p>;
	}
	return status === "in" ? <ShortsPage /> : <LoginPage />;
}

export default function StudioApp() {
	return (
		<AuthProvider>
			<Gate />
		</AuthProvider>
	);
}
