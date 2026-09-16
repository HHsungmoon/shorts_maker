import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PRODUCT_NAME } from "../shared/brand";
import { request } from "../shared/client";
import "./landing.css";

/**
 * 도메인을 그냥 눌렀을 때 만나는 첫 화면.
 *
 * 🔴 예전에는 `/` 가 스튜디오였다. 도메인을 누른 사람이 **관리자 로그인 화면**을 먼저 만났다는
 * 뜻이다 — 이 제품에서 수가 많은 쪽은 시청자인데 첫 화면이 남의 도구였다.
 *
 * 그래서 문 두 개만 있는 화면을 둔다. 설명을 길게 쓰지 않는다: 여기서 할 일은 읽는 것이 아니라
 * **어느 쪽인지 고르는 것**이고, 고르고 나면 각 화면이 자기 설명을 한다.
 *
 * 🔴 lazy 로 나누지 않는다. 가장 먼저 열리는 화면이라 코드 조각을 한 번 더 기다리게 할 이유가 없다.
 */
export function LandingPage() {
	// 보기 전용 비밀번호를 여기 적어 준다(2026-09-16). 해커톤 방문자가 관리자 면을 구경하러 오는데
	// 비밀번호를 물어볼 사람이 옆에 없기 때문이다. 그 역할은 GET 말고 아무것도 못 한다(서버가 403).
	//
	// 🔴 값을 상수로 박지 않고 서버에서 받는다. 박아 두면 `.env` 를 바꾼 순간 이 화면이 옛 값을
	// 보여주고, 그걸 그대로 친 방문자는 다섯 번 만에 잠긴다(로그인 실패 잠금).
	const [readonlyHint, setReadonlyHint] = useState<string | null>(null);

	useEffect(() => {
		let active = true;
		request<{ readonlyHint: string | null }>("/auth/me")
			.then((state) => {
				if (active) {
					setReadonlyHint(state.readonlyHint);
				}
			})
			// 🔴 첫 화면이 이 호출에 걸리면 안 된다. 실패하면 안내 한 줄이 빠질 뿐, 두 문은 그대로다.
			.catch(() => undefined);
		return () => {
			active = false;
		};
	}, []);

	return (
		<main className="landing">
			<h1 className="landing__title">{PRODUCT_NAME}</h1>
			<p className="landing__lead">
				긴 설명회 영상에서 <strong>궁금한 것만</strong> 잘라 숏폼으로 만듭니다.
			</p>

			<div className="landing__doors">
				<Link to="/watch" className="landing__door">
					<span className="landing__door-kind">시청자</span>
					<span className="landing__door-title">영상 보고 질문 남기기</span>
					<span className="landing__door-note">
						로그인이 없습니다. 궁금한 걸 남기면 그 답만 잘라 숏폼으로 올라옵니다.
					</span>
				</Link>

				<Link to="/studio" className="landing__door landing__door--studio">
					<span className="landing__door-kind">관리자</span>
					<span className="landing__door-title">질문에 답하고 숏폼 만들기</span>
					<span className="landing__door-note">
						비밀번호가 필요합니다. <strong>보기 전용 비밀번호</strong>로 들어오면 화면은 전부 볼 수
						있고 실행만 막힙니다.
					</span>
				</Link>
			</div>

			{readonlyHint && (
				<p className="landing__key">
					<strong>구경하러 오셨나요?</strong> 관리자 페이지 비밀번호에 <code>{readonlyHint}</code> 를
					넣으면 화면을 전부 둘러볼 수 있습니다. 이 비밀번호로는 영상 추가·클립 생성·발행 같은
					<strong> 실행만 막힙니다</strong> — 남의 데이터가 바뀌지 않으니 마음껏 눌러 보셔도 됩니다.
				</p>
			)}
		</main>
	);
}
