import { runIdeas } from "../api";
import { time } from "../../shared/format";
import type { ConceptKey, ShortsConcept, ShortsIdea } from "../types";
import type { SourceView } from "../SourcePage";

/**
 * 새 클립 탭 오른쪽 — AI 추천(tease §5-11, 2026-11-08).
 *
 * 영상의 구간 요약을 읽고 컨셉 셋(브랜딩 · 바이럴 · 정보성)마다 ① 이 영상이 얼마나 맞는지 ② 만들 만한 숏폼 주제를 낸다.
 * 주제를 누르면 왼쪽 칸에 컨셉 · 기준이 들어간다. 🔴 추천이 바로 클립이 되지 않는다 — 사람이 [구간 고르기] 를 누르고,
 * 고르기는 영상 전체에서 다시 점수를 매긴다(추천한 구간보다 나은 곳이 있으면 그쪽이 위로 온다).
 *
 * 적합도가 높은 컨셉이 위로 온다 — "이 영상은 무엇으로 만들면 좋은가" 가 첫눈에 읽혀야 한다.
 */
export function IdeasPanel({ view, onPick }: { view: SourceView; onPick: () => void }) {
	const {
		data,
		segments,
		submit,
		job,
		jobBusy,
		geminiReady,
		prepDone,
		concept,
		criteria,
		setConcept,
		setCriteria,
		setOpenPreview,
		clipBySegment,
	} = view;
	if (!data) {
		return null;
	}
	const ideas = data.ideas;
	const running = jobBusy && job?.kind === "ideas";
	const canRun = !jobBusy && prepDone && geminiReady;
	const labelOf = new Map<ConceptKey, ShortsConcept>(data.concepts.map((c) => [c.key, c]));

	// 적합도 높은 순. 판단이 없는 컨셉(null)은 아래로 — 순서 자체가 추천이다.
	const sections = (ideas?.concepts ?? [])
		.map((fit, order) => ({ ...fit, order, items: ideas?.ideas.filter((idea) => idea.concept === fit.concept) ?? [] }))
		.sort((a, b) => (b.fit ?? -1) - (a.fit ?? -1) || a.order - b.order);

	function pick(idea: ShortsIdea) {
		setConcept(idea.concept);
		setCriteria(idea.criteria);
		onPick();
	}

	return (
		<section className="sm-ideas" aria-label="AI 추천">
			<div className="sm-ideas__head">
				<div>
					<h2 className="sm-ideas__title">AI 추천</h2>
					<p className="sm-meta">이 영상으로 어떤 숏폼을 어떤 컨셉으로 만들지 제안합니다</p>
				</div>
				<button
					type="button"
					className={`button${ideas ? " button--small" : " sm-go"}`}
					disabled={!canRun}
					title={!geminiReady ? "GEMINI_API_KEY 가 없어 실행할 수 없습니다" : undefined}
					onClick={() => submit(() => runIdeas(data.source.id))}
				>
					{running ? "읽는 중…" : ideas ? "다시 추천" : "추천 받기"}
				</button>
			</div>

			{!ideas ? (
				<p className="sm-ideas__empty">
					{prepDone
						? `구간 요약 ${segments.length}개를 읽고, 컨셉마다 이 영상이 얼마나 맞는지와 만들 만한 주제를 냅니다. Gemini 1회입니다.`
						: "영상 준비가 끝나야 추천을 받을 수 있습니다."}
				</p>
			) : (
				<>
					{ideas.stale && (
						<p className="sm-make__need">구간을 다시 나눠 이 추천이 가리키던 구간이 바뀌었습니다. [다시 추천] 을 눌러 주세요.</p>
					)}
					<div className="sm-ideas__list">
						{sections.map((section) => {
							const info = labelOf.get(section.concept);
							return (
								<div key={section.concept} className="sm-ideas__concept">
									<div className="sm-ideas__fitrow">
										<span className="sm-ideas__name">{info?.label ?? section.concept}</span>
										<span className="sm-ideas__meter" aria-hidden="true">
											<span style={{ width: `${section.fit ?? 0}%` }} />
										</span>
										<span className="sm-ideas__fit">{section.fit === null ? "—" : section.fit}</span>
									</div>
									{section.note && <p className="sm-ideas__note">{section.note}</p>}
									{section.items.length === 0 ? (
										<p className="sm-meta">이 컨셉으로 낸 주제가 없습니다.</p>
									) : (
										<ul className="sm-ideas__items">
											{section.items.map((idea) => {
												const segment = segments.find((s) => s.id === idea.segment_id);
												const on = concept === idea.concept && criteria.trim() === idea.criteria;
												const made = segment ? clipBySegment.get(segment.idx) : undefined;
												return (
													<li key={`${idea.concept}-${idea.segment_id}`} className={`sm-idea${on ? " sm-idea--on" : ""}`}>
														<div className="sm-idea__top">
															<span className="sm-idea__title">{idea.title}</span>
															{made && <span className="sm-idea__made">클립 있음</span>}
														</div>
														<span className="sm-idea__where">
															#{idea.segment_idx + 1}
															{segment &&
																` · ${time(segment.start_sec)} · ${Math.max(1, Math.round((segment.end_sec - segment.start_sec) / 60))}분`}
														</span>
														{idea.reason && <p className="sm-idea__reason">{idea.reason}</p>}
														<div className="sm-idea__actions">
															<button
																type="button"
																className={`button button--small${on ? "" : " sm-go"}`}
																onClick={() => pick(idea)}
															>
																{on ? "왼쪽에 넣음" : "이 주제로"}
															</button>
															{segment && (
																<button
																	type="button"
																	className="button button--small"
																	onClick={() => setOpenPreview(segment.id)}
																>
																	미리보기
																</button>
															)}
														</div>
													</li>
												);
											})}
										</ul>
									)}
								</div>
							);
						})}
					</div>
					<p className="sm-meta">
						{new Date(ideas.createdAt).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" })} 추천
						{ideas.dropped > 0 && ` · 맞지 않아 버린 주제 ${ideas.dropped}개`}
					</p>
				</>
			)}
		</section>
	);
}
