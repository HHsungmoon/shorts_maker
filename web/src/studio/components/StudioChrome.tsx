import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { GutterPicker, ThemePicker } from "./ThemePicker";
import { LOGO_SRC, PRODUCT_NAME, REPO_NAME } from "../../shared/brand";
import { guideAttr } from "../guides/targets";
import { STAGE_LABEL } from "../useStudioJob";
import type { StudioJob } from "../useStudioJob";
import type { ShortsStatus } from "../types";
import "../shell.css";

// 화면들이 함께 쓰는 머리와 배너. 잡 상태 자체는 useStudioJob 이 들고 있다.
// 홈과 영상 화면은 상단바(shell.css)를 쓴다 — 첫 줄(StudioBrand)이 같고 둘째 줄부터 화면마다 다르다.
// 프롬프트 화면만 아직 옛 머리(StudioHead)다.

function elapsed(since: string): string {
	return `${Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 1000))}초`;
}

/**
 * 상단바 첫 줄 — 로고와 가이드 · 화면 설정 · 로그아웃. 홈과 영상 화면이 이 줄을 같이 쓴다(2026-11-15).
 * 홈이 옛 머리(가운데 1100px · 글자 제목)로 남아 있어서 영상 화면과 다른 서비스처럼 보였다.
 *
 * `onGuide` 를 준 화면에만 [가이드 보기] 가 뜬다. 안내가 없는 화면에 버튼을 두면 눌러도 아무 일이 없다 —
 * 지금은 홈만 안내가 있다(영상 화면의 탭별 안내는 다음 차례).
 */
export function StudioBrand({ onGuide }: { onGuide?: () => void }) {
	const { authRequired, signOut } = useAuth();
	return (
		<div className="sm-bar__brand">
			{/* 로고는 첫 화면(`/`)으로 간다. 영상 목록은 영상 화면의 ← 가 맡는다. */}
			<Link to="/" className="sm-bar__home" title="첫 화면">
				<img src={LOGO_SRC} alt={`${PRODUCT_NAME} 첫 화면`} />
			</Link>
			<span className="sm-bar__account">
				{/* 🔴 보기 전용도 누른다. 서버의 무엇도 바꾸지 않고, 물어볼 사람 없이 들어온 방문자에게 가장 필요하다. */}
				{onGuide && (
					// 폰에서는 글자를 숨기고 그림만 남는다(shell.css) — 그때도 이름이 남게 aria-label 을 단다.
					<button
						type="button"
						className="button button--small sm-bar__guide"
						{...guideAttr("guide")}
						aria-label="가이드 보기"
						title="가이드 보기"
						onClick={onGuide}
					>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
							<circle cx="12" cy="12" r="10" />
							<path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3" />
							<path d="M12 17h.01" />
						</svg>
						<span className="sm-bar__guide-label">가이드 보기</span>
					</button>
				)}
				{/* 폰에서는 숨긴다 — 여백 설정이 폰에서는 적용되지 않는다(styles.css). */}
				<span className="sm-bar__gutter">
					<GutterPicker />
				</span>
				<ThemePicker />
				{authRequired && (
					<button type="button" className="button button--small" onClick={signOut}>
						로그아웃
					</button>
				)}
			</span>
		</div>
	);
}

/** 옛 페이지 머리(프롬프트). 제목 옆 버튼만 화면마다 다르고 오른쪽 로그아웃은 같다. */
export function StudioHead({
	title,
	count,
	children,
}: {
	title: string;
	count?: ReactNode;
	children?: ReactNode;
}) {
	const { authRequired, signOut } = useAuth();
	return (
		<div className="page-head">
			<h1 className="page-title">{title}</h1>
			{count && <span className="page-count">{count}</span>}
			<span className="sm-actions sm-actions--end">
				{children}
				<ThemePicker />
				{authRequired && (
					<button type="button" className="button button--small" onClick={signOut}>
						로그아웃
					</button>
				)}
			</span>
		</div>
	);
}

/** 서버 상태·오류·잡 진행. 두 화면 모두 제목 바로 아래에 이 순서로 깐다. */
export function StudioBanners({
	status,
	studio,
	readonlyNotice = true,
}: {
	status: { data: ShortsStatus | null; error: Error | null };
	studio: StudioJob;
	/** 보기 전용 안내를 여기서 띄울까. 영상 화면은 상단바가 늘 보여주므로 끈다. */
	readonlyNotice?: boolean;
}) {
	const { job, jobBusy, foreignJob, error, notice } = studio;
	const { canAct } = useAuth();
	return (
		<>
			{/* 🔴 늘 보이게 둔다. 접어 두면 "왜 버튼이 안 먹지" 가 되고, 그게 심사 중에 일어나면
			    제품이 고장 난 것으로 보인다. */}
			{readonlyNotice && !canAct && (
				<div className="notice">
					보기 전용으로 로그인했습니다. 화면은 전부 볼 수 있고 실행·발행·수정만 막혀 있습니다.
				</div>
			)}
			{/* 거절 안내는 여기 배너가 아니라 화면 위 토스트다(components/DeniedToast). 이 배너는
			    본문 맨 위라, 아래쪽 버튼을 누른 사람 눈에는 아무 일도 안 일어난 것처럼 보였다. */}
			{status.error && (
				<div className="notice">
					서버 상태를 읽지 못했습니다. {REPO_NAME} 가 떠 있는지 확인하세요 ({status.error.message}).
				</div>
			)}
			{status.data && !status.data.geminiKey && (
				<div className="notice">GEMINI_API_KEY 가 없어 4·5단계를 실행할 수 없습니다.</div>
			)}
			{error && <p className="state state--error">{error}</p>}
			{notice && <div className="notice">{notice}</div>}
			{jobBusy && job && (
				<div className="notice">
					{STAGE_LABEL[job.kind] ?? job.kind} 진행 중… ({elapsed(job.createdAt)} 경과) · 끝날 때까지
					다른 실행은 대기합니다
				</div>
			)}
			{/* 🔴 남이 돌리는 중. 이 배너가 없으면 "왜 내 것만 거절되지" 의 답이 화면 어디에도 없다 —
			    서버는 한 번에 하나만 받는다(409). 누가 눌렀는지는 알 수 없다: 관리자 비밀번호가
			    하나라 사람을 구분하지 않는다. 그래서 **무엇이 언제부터**만 말한다. */}
			{foreignJob && (
				<div className="notice">
					다른 창에서 {STAGE_LABEL[foreignJob.kind] ?? foreignJob.kind} 실행 중…
					({elapsed(foreignJob.createdAt)} 경과) · 한 번에 하나만 돌기 때문에 지금 실행을 누르면
					거절됩니다
				</div>
			)}
		</>
	);
}
