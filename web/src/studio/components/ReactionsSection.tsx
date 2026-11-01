import { useMemo } from "react";
import { Link } from "react-router-dom";
import { summarizeReactions } from "../api";
import type { ShortsReactions, ShortsReactionTheme, ShortsReview } from "../types";
import type { SourceView } from "../SourcePage";

/**
 * 시청자 반응 — 질문이 아닌 유튜브 댓글(tease §3-6, 2026-11-01).
 *
 * 감상 댓글은 질문 탭에서 다룰 것이 없지만 **이 회차가 어떻게 받아들여졌나**를 말한다. 리포트는 윗선에 보내는
 * 산출물이라 여기 둔다. 위에 AI 요약, 아래에 원문 전부.
 *
 * 🔴 **원문을 전부 보인다.** 요약은 원문을 대신하지 않는다 — 요약이 빠뜨린 한 줄이 가장 중요한 말일 수 있다.
 * 🔴 묶음의 "N명" 은 **근거 댓글 수**다(서버가 번호를 검증해 센 것). 모델이 말한 숫자가 아니다.
 * 🔴 [AI 요약] 은 보기 전용이어도 잠그지 않는다 — `view.submit` 이 `refuse()` 로 이유를 말한다.
 */
export function ReactionsSection({ view, reactions }: { view: SourceView; reactions: ShortsReactions }) {
	const { sourceId, submit, jobBusy, sourceBusy } = view;
	const busy = jobBusy || sourceBusy;
	const { reviews, summary } = reactions;
	// 근거 번호 → 원문. 요약 묶음을 펼치면 그 댓글들이 보인다.
	const byId = useMemo(() => new Map(reviews.map((r) => [r.id, r])), [reviews]);
	// 원문 목록의 번호(1부터). 묶음의 근거가 목록 어디인지 같은 번호로 가리킨다.
	const order = useMemo(() => new Map(reviews.map((r, n) => [r.id, n + 1])), [reviews]);

	return (
		<section className="sm-report__card sm-reactions" aria-label="시청자 반응">
			<div className="sm-reactions__top">
				<h3 className="sm-report__head">
					시청자 반응 <span className="sm-meta">감상 댓글 {reviews.length}개</span>
				</h3>
				{reviews.length > 0 && (
					<button
						type="button"
						className="button button--small sm-actions--end"
						disabled={busy}
						title={busy ? "다른 작업이 끝나야 실행할 수 있습니다" : "감상 댓글을 AI 로 한 번 요약합니다(Gemini 1회)"}
						onClick={() => submit(() => summarizeReactions(sourceId))}
					>
						{summary ? "AI 요약 다시" : "AI 요약"}
					</button>
				)}
			</div>
			<p className="sm-meta sm-report__hint">
				{reactions.total === 0 ? (
					<>
						아직 댓글을 가져오지 않았습니다.{" "}
						<Link to={`/studio/sources/${sourceId}/questions`}>질문 탭</Link>에서 [댓글에서 질문 가져오기] 를
						누르면, 질문이 아닌 댓글이 여기 모입니다.
					</>
				) : (
					<>
						유튜브 댓글 {reactions.total}개 중 질문 꼴이 아닌 것입니다. 질문 {reactions.questions}개는{" "}
						<Link to={`/studio/sources/${sourceId}/questions`}>질문 탭</Link>에 있습니다.
						{reactions.fetchedAt && ` 마지막으로 가져온 때 ${dateTime(reactions.fetchedAt)}.`}
					</>
				)}
			</p>

			{summary && (
				<div className="sm-reactions__summary">
					{summary.stale && (
						<p className="sm-reactions__stale">
							요약한 뒤 댓글이 바뀌었습니다(그때 {summary.reviews}개 → 지금 {reviews.length}개). [AI 요약 다시] 를
							누르면 새로 요약합니다.
						</p>
					)}
					{summary.overview && <p className="sm-reactions__overview">{summary.overview}</p>}
					{summary.themes.length > 0 && (
						<ul className="sm-reactions__themes">
							{summary.themes.map((theme, n) => (
								<Theme key={n} theme={theme} byId={byId} order={order} />
							))}
						</ul>
					)}
					<p className="sm-meta sm-report__note">
						AI 요약 · {dateTime(summary.createdAt)} · 사람 수는 근거로 짚은 댓글 수입니다.
					</p>
				</div>
			)}

			{reviews.length > 0 && (
				<details className="sm-reactions__all" open={!summary}>
					<summary>댓글 원문 {reviews.length}개</summary>
					<ol className="sm-reactions__list">
						{reviews.map((review) => (
							<li key={review.id} value={order.get(review.id)}>
								{review.text}
								{review.publishedAt && <span className="sm-meta"> · {date(review.publishedAt)}</span>}
							</li>
						))}
					</ol>
				</details>
			)}
		</section>
	);
}

function Theme({
	theme,
	byId,
	order,
}: {
	theme: ShortsReactionTheme;
	byId: Map<number, ShortsReview>;
	order: Map<number, number>;
}) {
	return (
		<li className="sm-reactions__theme">
			<p className="sm-reactions__title">
				{/* 🔴 종류를 색만으로 말하지 않는다 — 글자 딱지가 같이 붙는다(막대표와 같은 규칙). */}
				<span className={`sm-reactions__tag sm-reactions__tag--${theme.kind}`}>{theme.label}</span>
				{theme.title}
				<span className="sm-meta"> · {theme.count}명</span>
			</p>
			{theme.detail && <p className="sm-reactions__detail">{theme.detail}</p>}
			<details className="sm-report__texts">
				<summary>근거 댓글 {theme.count}개</summary>
				<ul className="sm-report__list">
					{theme.comments.map((id) => (
						<li key={id}>
							<span className="sm-meta">{order.get(id)}. </span>
							{byId.get(id)?.text}
						</li>
					))}
				</ul>
			</details>
		</li>
	);
}

/**
 * 리포트 "글로 복사" 에 붙는 덩어리. 🔴 마크다운 기호를 쓰지 않는다(asText 와 같은 규칙).
 * 원문은 싣지 않는다 — 윗선에 보내는 글에 시청자 댓글을 통째로 붙이면 요약을 읽지 않는다. 화면에는 원문이 다 있다.
 */
export function reactionsText(reactions: ShortsReactions): string[] {
	if (reactions.reviews.length === 0) return [];
	const lines = ["", `[시청자 반응 — 감상 댓글 ${reactions.reviews.length}개]`];
	const { summary } = reactions;
	if (!summary) {
		lines.push("  아직 요약하지 않았습니다.");
		return lines;
	}
	if (summary.overview) lines.push(`  ${summary.overview}`);
	for (const theme of summary.themes) {
		lines.push(`  · ${theme.label}: ${theme.title} (${theme.count}명)`);
		if (theme.detail) lines.push(`    ${theme.detail}`);
	}
	if (summary.stale) lines.push(`  (요약 뒤 댓글이 바뀌었습니다 — 요약 때 ${summary.reviews}개)`);
	return lines;
}

function date(iso: string): string {
	return new Date(iso).toLocaleDateString("ko-KR");
}

function dateTime(iso: string): string {
	return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}
