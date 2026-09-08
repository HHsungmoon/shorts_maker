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
	const { job, jobBusy, error, notice } = studio;
	return (
		<>
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
		</>
	);
}
