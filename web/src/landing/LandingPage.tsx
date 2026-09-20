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
				<section className="landing__key">
					<p className="landing__key-head">{PRODUCT_NAME} 서비스에 관심을 가져주셔서 감사합니다.</p>
					<p className="landing__key-line">
						관리자 페이지 비밀번호에 <code>{readonlyHint}</code> 를 넣으면 관리자 화면을 모두
						구경하실 수 있습니다. 이 비밀번호로는 영상 추가·클립 생성·발행 같은{" "}
						<strong>실행만 막힙니다</strong> — 기존의 데이터가 바뀌지 않으니 마음껏 눌러 보셔도 됩니다.
					</p>
					<p className="landing__key-line">
						{PRODUCT_NAME}에 궁금하거나 문의사항이 있으시다면 이메일로 편하게 연락 주시면
						감사하겠습니다.
					</p>
					{/* 운영자가 직접 공개하기로 한 연락처다. mailto 로 걸어 두면 한 번에 쓸 수 있다. */}
					<p className="landing__key-mail">
						Email : <a href="mailto:sunmoonkr@gmail.com">sunmoonkr@gmail.com</a>
					</p>
				</section>
			)}

			<ThanksNote />
		</main>
	);
}

/**
 * 영상을 쓰게 해 주신 곳에 대한 감사 인사.
 *
 * 🔴 **이 제품은 남의 영상으로 굴러간다.** 허락을 받아서 쓴다는 사실이 첫 화면에 보이는 편이
 * 맞고, 구석의 작은 각주보다 눈에 띄는 자리가 낫다 — 감사는 크게 하는 것이다.
 *
 * 하트는 장식이라 `aria-hidden` 이다. 화면 낭독기가 "path path path" 를 읽으면 문장이 끊긴다.
 */
function ThanksNote() {
	return (
		<section className="thanks" aria-label="감사 인사">
			<Heart className="thanks__heart thanks__heart--a" />
			<Heart className="thanks__heart thanks__heart--b" />
			<Heart className="thanks__heart thanks__heart--c" />
			<Heart className="thanks__heart thanks__heart--d" />
			<p className="thanks__line">
				영상 사용을 허가해 주신
				<br />
				<strong>서강대학교</strong>에 감사합니다.
			</p>
			<p className="thanks__sign">— Team TEASE</p>
		</section>
	);
}

function Heart({ className }: { className: string }) {
	return (
		<svg className={className} viewBox="0 0 24 22" aria-hidden="true" focusable="false">
			{/* 선으로만 그린다 — 꽉 찬 하트는 무겁고, 손으로 그린 느낌은 획에서 온다. */}
			<path
				d="M12 20.5C12 20.5 1.8 14.2 1.8 7.6 1.8 4.2 4.4 1.8 7.4 1.8c2 0 3.7 1.1 4.6 2.8.9-1.7 2.6-2.8 4.6-2.8 3 0 5.6 2.4 5.6 5.8 0 6.6-10.2 12.9-10.2 12.9z"
				fill="none"
				stroke="currentColor"
				strokeWidth="1.6"
				strokeLinecap="round"
				strokeLinejoin="round"
			/>
		</svg>
	);
}
