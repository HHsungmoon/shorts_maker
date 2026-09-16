import { Link } from "react-router-dom";
import { PRODUCT_NAME } from "../shared/brand";
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
		</main>
	);
}
