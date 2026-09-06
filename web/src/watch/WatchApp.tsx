import { Link, Route, Routes } from "react-router-dom";
import { PRODUCT_NAME } from "../shared/brand";
import { HomePage } from "./HomePage";
import { SourcePage } from "./SourcePage";
import "./watch.css";

// 시청자 트리. 🔴 AuthProvider 가 없다 — 여기서 `/auth/me` 를 부르면 로그인하지 않은 사람에게
// 401 이 나고 콘솔이 빨개진다. 신원은 서버가 발급하는 익명 쿠키가 전부다.
export default function WatchApp() {
	return (
		<div className="watch">
			<header className="watch-top">
				<Link to="/watch" className="watch-wordmark">
					{PRODUCT_NAME}
				</Link>
				<p className="watch-tagline">궁금한 걸 남기면, 그 답만 잘라 숏폼으로 만들어 드립니다</p>
			</header>

			<main className="watch-main">
				<Routes>
					<Route index element={<HomePage />} />
					<Route path=":sourceId" element={<SourcePage />} />
					<Route path="*" element={<p className="state">없는 페이지입니다.</p>} />
				</Routes>
			</main>
		</div>
	);
}
