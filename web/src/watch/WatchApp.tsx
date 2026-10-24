import { useState } from "react";
import { Link, Route, Routes, useMatch } from "react-router-dom";
import { LOGO_SRC, PRODUCT_NAME } from "../shared/brand";
import { PrefMenu } from "../shared/PrefMenu";
import { HomePage } from "./HomePage";
import { WATCH_LAYOUTS, loadWatchLayout, saveWatchLayout } from "./layout";
import type { WatchLayout } from "./layout";
import { SourcePage } from "./SourcePage";
import "./watch.css";

// 시청자 트리. 🔴 AuthProvider 가 없다 — 여기서 `/auth/me` 를 부르면 로그인하지 않은 사람에게
// 401 이 나고 콘솔이 빨개진다. 신원은 서버가 발급하는 익명 쿠키가 전부다.
export default function WatchApp() {
	// 레이아웃은 여기서 들고 영상 화면에 내려준다 — 고르기 메뉴는 머리에, 쓰는 곳은 본문이다.
	const [layout, setLayout] = useState<WatchLayout>(loadWatchLayout);
	// 고르기는 영상 화면에서만 뜻이 있다. 목록 화면에 두면 눌러도 아무것도 안 바뀐다.
	const onSource = useMatch("/watch/:sourceId") !== null;

	return (
		<div className={`watch${layout === "shelf" && onSource ? " watch--wide" : ""}`}>
			<header className="watch-top">
				<Link to="/watch" className="watch-home" title="영상 목록">
					<img src={LOGO_SRC} alt={`${PRODUCT_NAME} 영상 목록`} />
				</Link>
				<p className="watch-tagline">궁금한 걸 남기면, 그 답만 잘라 숏폼으로 만들어 드립니다</p>
				{onSource && (
					<span className="watch-top__prefs">
						<PrefMenu
							label="레이아웃"
							options={WATCH_LAYOUTS}
							load={loadWatchLayout}
							save={(id) => {
								saveWatchLayout(id);
								setLayout(id as WatchLayout);
							}}
							icon={(option) => (
								<span
									className={`watch-layout-icon watch-layout-icon--${option.id}`}
									aria-hidden="true"
								>
									<span />
									<span />
								</span>
							)}
						/>
					</span>
				)}
			</header>

			<main className="watch-main">
				<Routes>
					<Route index element={<HomePage />} />
					<Route path=":sourceId" element={<SourcePage layout={layout} />} />
					<Route path="*" element={<p className="state">없는 페이지입니다.</p>} />
				</Routes>
			</main>
		</div>
	);
}
