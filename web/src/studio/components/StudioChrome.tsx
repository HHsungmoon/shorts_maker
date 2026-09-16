import type { ReactNode } from "react";
import { useAuth } from "../AuthContext";
import { REPO_NAME } from "../../shared/brand";
import { STAGE_LABEL } from "../useStudioJob";
import type { StudioJob } from "../useStudioJob";
import type { ShortsStatus } from "../types";

// 두 화면(홈 · 영상)이 함께 쓰는 머리와 배너. 잡 상태 자체는 useStudioJob 이 들고 있다.

function elapsed(since: string): string {
	return `${Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 1000))}초`;
}

/**
 * 페이지 머리. 제목 왼쪽에 무엇을 두느냐만 화면마다 다르고(홈은 없음, 영상은 목록으로 돌아가기)
 * 오른쪽 로그아웃은 같다.
 */
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
}: {
	status: { data: ShortsStatus | null; error: Error | null };
	studio: StudioJob;
}) {
	const { job, jobBusy, foreignJob, error, notice } = studio;
	const { canAct } = useAuth();
	return (
		<>
			{/* 🔴 늘 보이게 둔다. 접어 두면 "왜 버튼이 안 먹지" 가 되고, 그게 심사 중에 일어나면
			    제품이 고장 난 것으로 보인다. */}
			{!canAct && (
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
