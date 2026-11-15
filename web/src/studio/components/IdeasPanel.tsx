import { Link } from "react-router-dom";
import { cutIdea, renderClip, runCut, runIdeas } from "../api";
import { time } from "../../shared/format";
import type { ConceptKey, ShortsClip, ShortsConcept, ShortsIdea, ShortsIdeas, ShortsSegment } from "../types";
import type { SourceView } from "../SourcePage";

/**
 * 새 클립 탭의 첫 번째 길 — AI 추천 클립(tease §5-11).
 *
 * 영상의 구간 요약을 읽고 컨셉 셋(브랜딩 · 바이럴 · 정보성)마다 ① 이 영상이 얼마나 맞는지 ② 만들 만한 숏폼 주제를 낸다.
 * 컨셉 셋을 적합도 순으로 나란히 세운다 — "이 영상은 무엇으로 만들면 좋은가" 가 왼쪽부터 읽힌다.
 *
 * 2026-11-14: 카드에서 바로 [클립 만들기] 다. 처음엔 주제를 누르면 기준 칸에 들어가고 [구간 고르기] 로 다시 점수를 매겼는데,
 * 고르기가 23개씩 내놓으니 추천 둘이 그 안에 묻혀 따로 둘 이유가 없었다. 누르면 주제 하나짜리 run 을 만들고 자른다.
 * 🔴 그 구간을 쓰는 클립이 이미 있으면(어느 길에서 나왔든) 만들지 않고 "이미 있습니다" 와 그 클립을 보여 준다 — 서버도 409 다.
 */
export function IdeasPanel({ view }: { view: SourceView }) {
	const { data, segments, submit, job, jobBusy, geminiReady, prepDone } = view;
	if (!data) {
		return null;
	}
	const ideas = data.ideas;
	const running = jobBusy && job?.kind === "ideas";
	const canRun = !jobBusy && prepDone && geminiReady;
	const labelOf = new Map<ConceptKey, ShortsConcept>(data.concepts.map((c) => [c.key, c]));
	// 클립이 이미 쓰는 구간 — 다음 추천에서 빠진다(서버). 처음 받기 전에 몇 개가 빠지는지 미리 말해 둔다.
	const taken = new Set(data.clips.flatMap((clip) => clip.segment_ids));
	const fresh = segments.filter((s) => !taken.has(s.id)).length;

	// 적합도 높은 순. 판단이 없는 컨셉(null)은 뒤로 — 순서 자체가 추천이다.
	const sections = (ideas?.concepts ?? [])
		.map((fit, order) => ({ ...fit, order, items: ideas?.ideas.filter((idea) => idea.concept === fit.concept) ?? [] }))
		.sort((a, b) => (b.fit ?? -1) - (a.fit ?? -1) || a.order - b.order);

	return (
		<section className="sm-ideas" role="tabpanel" aria-label="AI 추천 클립">
			<div className="sm-ideas__head">
				<p className="sm-ideas__lead">
					컨셉마다 이 영상이 얼마나 맞는지와 만들 만한 주제입니다. 주제는 컨셉마다 최대 5개, 65점 이상만 남깁니다.
					이미 클립이 있는 구간은 추천하지 않습니다.
				</p>
				<button
					type="button"
					className={`button${ideas ? "" : " sm-go"}`}
					disabled={!canRun || fresh === 0}
					title={
						!geminiReady
							? "GEMINI_API_KEY 가 없어 실행할 수 없습니다"
							: fresh === 0 && prepDone
								? "모든 구간에 이미 클립이 있어 추천할 구간이 없습니다"
								: undefined
					}
					onClick={() => submit(() => runIdeas(data.source.id))}
				>
					{running ? "읽는 중…" : ideas ? "다시 추천" : "추천 받기"}
				</button>
			</div>

			{!ideas ? (
				<p className="sm-ideas__empty">
					{!prepDone
						? "영상 준비가 끝나야 추천을 받을 수 있습니다."
						: fresh === 0
							? "모든 구간에 이미 클립이 있어 추천할 구간이 없습니다."
							: `구간 요약 ${fresh}개를 읽고${segments.length > fresh ? `(클립이 있는 ${segments.length - fresh}개는 뺍니다)` : ""}, 컨셉마다 이 영상이 얼마나 맞는지와 만들 만한 주제를 냅니다. Gemini 1회입니다.`}
				</p>
			) : (
				<>
					{ideas.stale && (
						<p className="sm-make__need">구간을 다시 나눠 이 추천이 가리키던 구간이 바뀌었습니다. [다시 추천] 을 눌러 주세요.</p>
					)}
					<div className="sm-ideas__cols">
						{sections.map((section) => (
							<div key={section.concept} className="sm-ideas__concept">
								<div className="sm-ideas__fitrow">
									<span className="sm-ideas__name">{labelOf.get(section.concept)?.label ?? section.concept}</span>
									<span className="sm-ideas__meter" aria-hidden="true">
										<span style={{ width: `${section.fit ?? 0}%` }} />
									</span>
									<span className="sm-ideas__fit">{section.fit === null ? "—" : section.fit}</span>
								</div>
								{section.note && <p className="sm-ideas__note">{section.note}</p>}
								{section.items.length === 0 ? (
									<p className="sm-meta">
										{ideas.minScore !== null
											? `${ideas.minScore}점을 넘는 주제가 없습니다.`
											: "이 컨셉으로 낸 주제가 없습니다."}
									</p>
								) : (
									<ul className="sm-ideas__items">
										{section.items.map((idea) => (
											<IdeaCard key={`${idea.concept}-${idea.segment_id}`} idea={idea} ideas={ideas} view={view} />
										))}
									</ul>
								)}
							</div>
						))}
					</div>
					<p className="sm-meta">
						{new Date(ideas.createdAt).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" })} 추천
						{ideas.minScore !== null && ideas.below > 0 && ` · ${ideas.minScore}점 미만이라 뺀 주제 ${ideas.below}개`}
						{ideas.dropped > 0 && ` · 맞지 않아 버린 주제 ${ideas.dropped}개`}
					</p>
				</>
			)}
		</section>
	);
}

/** 그 클립이 어디서 나왔고 지금 어디까지 갔나 — "이미 있습니다" 옆에 붙는 한 줄. */
function describe(clip: ShortsClip): string {
	const from = clip.question ? "시청자 질문" : clip.idea_id !== null ? "AI 추천" : "직접 고르기";
	const state =
		clip.uploads.length > 0
			? "유튜브에 올림"
			: clip.published_at !== null
				? "공개 중"
				: clip.rendered
					? "렌더됨"
					: "렌더 전";
	return `${from} · ${state}`;
}

/**
 * 추천 카드 하나. 상태가 넷이다 —
 *   만들 수 있다      → [클립 만들기]
 *   이 카드로 만들었다 → 결과 카드와 같은 다음 걸음(다시 만들기 · 렌더하기 · 공개 탭)
 *   이미 있다         → 그 구간을 쓰는 다른 클립(질문 · 직접 고르기 · 다른 추천)을 보여 주고 만들지 않는다
 *   구간이 사라졌다    → 구간을 다시 나눠 만들 수 없다
 */
function IdeaCard({ idea, ideas, view }: { idea: ShortsIdea; ideas: ShortsIdeas; view: SourceView }) {
	const { data, segments, submit, job, jobBusy, geminiReady, setOpenPreview, sourceId } = view;
	const clips = data?.clips ?? [];
	const segment: ShortsSegment | undefined = segments.find((s) => s.id === idea.segment_id);
	const own = clips.find(
		(clip) => clip.idea_id === ideas.id && clip.concept === idea.concept && clip.segment_id === idea.segment_id,
	);
	// 같은 구간을 쓰는 다른 클립. 여럿이면 밖에 나간 것(올림 · 공개)을 먼저 보인다 — 겹치면 곤란한 쪽이 그것이다.
	const others = own ? [] : clips.filter((clip) => clip.segment_ids.includes(idea.segment_id));
	const other = others.find((clip) => clip.uploads.length > 0 || clip.published_at !== null) ?? others[0];
	const making = jobBusy && job?.kind === "cut" && job.target === `segment ${idea.segment_id}`;

	return (
		<li className={`sm-idea${other ? " sm-idea--dup" : ""}${own ? " sm-idea--made" : ""}`}>
			<div className="sm-idea__top">
				{/* 결과 카드와 같은 자리 · 모양 — 점수가 먼저 눈에 들어온다. 2026-11-14 전의 추천에는 없다. */}
				{idea.score != null && <span className="sm-idea__score">{idea.score}</span>}
				<span className="sm-idea__title">{idea.title}</span>
				{own && <span className="sm-idea__badge">만듦</span>}
				{other && <span className="sm-idea__badge sm-idea__badge--dup">이미 있음</span>}
			</div>
			<span className="sm-idea__where">
				#{idea.segment_idx + 1}
				{segment &&
					` · ${time(segment.start_sec)} · ${Math.max(1, Math.round((segment.end_sec - segment.start_sec) / 60))}분`}
			</span>
			{idea.reason && <p className="sm-idea__reason">{idea.reason}</p>}

			{other && (
				<p className="sm-idea__dup">
					<span className="sm-idea__duplabel">이미 있습니다</span>
					<span className="sm-idea__dupwhat" title={other.title ?? undefined}>
						{other.title ?? `클립 ${other.id}`}
					</span>
					<span className="sm-meta">{describe(other)}</span>
				</p>
			)}

			<div className="sm-idea__actions">
				{segment && (
					<button type="button" className="button button--small" onClick={() => setOpenPreview(segment.id)}>
						미리보기
					</button>
				)}
				{!segment ? (
					<span className="sm-meta">구간이 바뀌어 만들 수 없습니다</span>
				) : other ? (
					<Link className="sm-card__link" to={`/studio/sources/${sourceId}/clips`}>
						공개 탭에서 보기 →
					</Link>
				) : own ? (
					<>
						<button
							type="button"
							className="button button--small"
							disabled={jobBusy || !geminiReady}
							onClick={() => submit(() => runCut(own.run_id, segment.id, true))}
						>
							다시 만들기
						</button>
						{/* 결과 카드와 같다 — 렌더를 안 하면 공개 탭에서 재생할 수도 공개할 수도 없다. */}
						{!own.rendered ? (
							<button
								type="button"
								className="button button--small sm-go"
								disabled={jobBusy}
								onClick={() => submit(() => renderClip(own.id))}
							>
								렌더하기
							</button>
						) : (
							<Link className="sm-card__link" to={`/studio/sources/${sourceId}/clips`}>
								공개 탭에 있음 →
							</Link>
						)}
					</>
				) : (
					<button
						type="button"
						className="button button--small sm-go"
						disabled={jobBusy || !geminiReady}
						onClick={() => submit(() => cutIdea(sourceId, ideas.id, idea.concept, segment.id))}
					>
						{making ? "만드는 중…" : "클립 만들기"}
					</button>
				)}
			</div>
		</li>
	);
}
