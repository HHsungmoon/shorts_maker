import {
	clipUrl,
	patchClipTitle,
	publishClip,
	renderClip,
	reviewClip,
	runCut,
	runRank,
	segmentPreviewUrl,
} from "../api";
import { ClipVideo } from "./ClipVideo";
import { Step } from "./Step";
import { time } from "../format";
import type { SourceView } from "../SourcePage";

// 후보를 한 번에 몇 개 보여줄까. 구간이 40개면 후보도 40개라 전부 그리면 화면이 그것만으로 찬다.
const RANKED_PREVIEW = 7;

/**
 * 클립 탭 — 만들어진 클립과, 기준을 직접 적어 구간을 고르는 길(5~7단계).
 */
export function ClipsTab({ view }: { view: SourceView }) {
	const {
		data,
		geminiReady,
		jobBusy,
		submit,
		act,
		reload,
		segments,
		latestRun,
		ranked,
		excluded,
		clipBySegment,
		clipGroups,
		nextStep,
		criteria,
		setCriteria,
		openPreview,
		setOpenPreview,
		showAllRanked,
		setShowAllRanked,
		titleEdit,
		setTitleEdit,
		titleDraft,
		setTitleDraft,
	} = view;

	return (
		<>
			{data && data.clips.length > 0 && (
				<>
					<h2 className="section-title">완성 클립</h2>
					{/* 🔴 출처별로 묶는다. 시청자 질문에 답한 클립과 크리에이터가 자기 기준으로 뽑은
					    클립이 한 목록에 섞이면 "클립 3" 이라는 이름만으로는 무엇의 답인지 알 수 없다.
					    묶음의 제목이 곧 그 클립이 답하는 질문(또는 그때 쓴 기준)이다. */}
					{clipGroups.map((group) => (
					<div key={group.key}>
						<h3 className="sm-group">
							{group.question ? (
								<>
									<span className="sm-group__kind">시청자 질문</span> {group.question}
									{group.askedBy > 1 && (
										<span className="sm-meta"> · {group.askedBy}명이 물어봤어요</span>
									)}
								</>
							) : (
								<>
									<span className="sm-group__kind sm-group__kind--own">내 기준</span>{" "}
									{group.criteria ?? "기준 없음"}
								</>
							)}
						</h3>
					{group.clips.map((clip) => (
						<section className="sm-panel sm-clip" key={clip.id}>
							{clip.rendered ? (
								<ClipVideo src={clipUrl(clip.id)} width={220} />
							) : (
								<button
									type="button"
									className="button button--small sm-go"
									disabled={jobBusy}
									onClick={() => submit(() => renderClip(clip.id))}
								>
									7. 렌더하기
								</button>
							)}
							<div style={{ flex: 1, minWidth: 0 }}>
								{/* 🔴 시청자 목록에 그대로 보이는 문장이다. 기본값은 질문이나 구간 설명이라
								    제목으로 쓰라고 쓴 문장이 아니다 — 여기서 다듬는다. */}
								{titleEdit === clip.id ? (
									<div className="sm-actions">
										<input
											className="field__input"
											style={{ flex: 1, minWidth: 0 }}
											value={titleDraft}
											autoFocus
											maxLength={200}
											onChange={(event) => setTitleDraft(event.target.value)}
										/>
										<button
											type="button"
											className="button button--small sm-go"
											disabled={titleDraft.trim().length === 0}
											onClick={() =>
												act(async () => {
													await patchClipTitle(clip.id, titleDraft);
													setTitleEdit(null);
													reload();
												})
											}
										>
											저장
										</button>
										<button
											type="button"
											className="button button--small"
											onClick={() => setTitleEdit(null)}
										>
											취소
										</button>
									</div>
								) : (
									<div className="sm-actions">
										<strong style={{ flex: 1, minWidth: 0 }}>
											{clip.title ?? `클립 ${clip.id}`}
										</strong>
										<button
											type="button"
											className="button button--small"
											onClick={() => {
												setTitleEdit(clip.id);
												setTitleDraft(clip.title ?? "");
											}}
										>
											제목 고치기
										</button>
									</div>
								)}
								<div>
									<span className="sm-meta">
										클립 {clip.id} ·{" "}
										{time(clip.start_sec)}~{time(clip.end_sec)} ·{" "}
										{Math.round(clip.end_sec - clip.start_sec)}초
										{clip.score !== null && ` · ${clip.score}점`}
									</span>
								</div>
								<p>{clip.reason}</p>
								<div className="sm-actions">
									<button
										type="button"
										className="button button--small"
										onClick={() => act(() => reviewClip(clip, "OK"))}
									>
										쓸만함
									</button>
									<button
										type="button"
										className="button button--small button--danger"
										onClick={() => act(() => reviewClip(clip, "NG"))}
									>
										아님
									</button>
									{clip.reviews.length > 0 && (
										<span className="sm-meta">
											평가 {clip.reviews.map((r) => r.verdict).join(", ")}
										</span>
									)}
									{/* 🔴 질문에 답한 클립은 질문 패널에서 발행한다(클러스터 상태가 함께 움직여야
									    하므로). 여기 있는 건 크리에이터가 자기 기준으로 뽑은 클립이라 묶인 질문이
									    없다 — 그래도 시청자에게 보여줄 수 있어야 해서 발행 길을 따로 둔다. */}
									{clip.rendered && (
										<button
											type="button"
											className={`button button--small${clip.published_at ? "" : " sm-go"}`}
											disabled={jobBusy}
											onClick={() =>
												act(async () => {
													await publishClip(clip.id, !clip.published_at);
													reload();
												})
											}
										>
											{clip.published_at ? "내리기" : "시청자에게 공개"}
										</button>
									)}
									{clip.published_at && (
										<a
											className="button button--small"
											href={`/watch/${data.source.id}`}
											target="_blank"
											rel="noreferrer"
										>
											시청자 화면 열기
										</a>
									)}
								</div>
							</div>
						</section>
					))}
					</div>
					))}
				</>
			)}

			{/* 🔴 여기는 시청자 질문에 답하는 길과 **다른 길**이다 — 크리에이터가 자기 기준을 적어
			    구간을 직접 고른다. 준비 단계(1~4) 안에 두면 반드시 거쳐야 하는 것처럼 보이는데
			    실제로는 선택이다. 기본은 접어 둔다 — 주된 길은 질문 탭의 질문 루프다. */}
			{data && (
				<details className="sm-fold sm-own">
					<summary>
						직접 골라 만들기{ranked.length > 0 && ` · 후보 ${ranked.length}개`}
					</summary>
					<p className="sm-meta sm-own__lead">
						시청자 질문과 별개로, 기준을 직접 적어 구간을 고릅니다
					</p>

					<Step
						no={5}
						title="선정"
						done={ranked.length > 0}
						next={nextStep === 5}
						detail={ranked.length > 0 ? `${ranked.length}개 후보` : "기준에 맞는 구간을 고릅니다"}
						actions={
							data && (
								<>
									<input
										className="field__input"
										style={{ width: 260 }}
										value={criteria}
										placeholder="비우면 자립성만 봅니다"
										onChange={(e) => setCriteria(e.target.value)}
										aria-label="기준 프롬프트"
									/>
									<button
										type="button"
										className={`button button--small${nextStep === 5 ? " sm-go" : ""}`}
										disabled={jobBusy || segments.length === 0 || !geminiReady}
										onClick={() => submit(() => runRank(data.source.id, criteria.trim() || null))}
									>
										{ranked.length > 0 ? "다시 선정" : "실행"}
									</button>
								</>
							)
						}
					/>

					{ranked.length > 0 && (
						<>
							<h3 className="section-title">
								6·7단계 — 구간을 골라 클립을 만들고 렌더합니다
								{latestRun?.criteria_prompt && (
									<span className="page-count"> · 기준: {latestRun.criteria_prompt}</span>
								)}
							</h3>
							{latestRun?.error && <p className="state state--error">{latestRun.error}</p>}

							{ranked.slice(0, showAllRanked ? undefined : RANKED_PREVIEW).map((entry, rank) => {
								const segment = segments.find((s) => s.idx === entry.idx);
								const clip = clipBySegment.get(entry.idx);
								return (
									<article className="sm-item" key={entry.idx}>
										<div className="sm-item__head">
											<span className="sm-item__rank">#{rank + 1}</span>
											<span className="sm-score">{entry.score}점</span>
											{segment && (
												<span className="sm-meta">
													{time(segment.start_sec)}~{time(segment.end_sec)} ·{" "}
													{Math.round((segment.end_sec - segment.start_sec) / 60)}분
												</span>
											)}
											{clip && (
												<span className="sm-badge">
													클립 {clip.id} · {Math.round(clip.end_sec - clip.start_sec)}초
												</span>
											)}
											<span className="sm-actions sm-actions--end">
												{segment && (
													<button
														type="button"
														className="button button--small"
														onClick={() =>
															setOpenPreview(openPreview === segment.id ? null : segment.id)
														}
													>
														{openPreview === segment.id ? "미리보기 닫기" : "미리보기"}
													</button>
												)}
												{segment && latestRun && (
													<button
														type="button"
														className={clip ? "button button--small" : "button button--small sm-go"}
														disabled={jobBusy || !geminiReady}
														onClick={() =>
															submit(() => runCut(latestRun.id, segment.id, Boolean(clip)))
														}
													>
														{clip ? "다시 만들기" : "6. 클립 만들기"}
													</button>
												)}
											</span>
										</div>

										{segment?.description && <p className="sm-item__title">{segment.description}</p>}
										<p className="sm-item__reason">{entry.reason}</p>

										{segment && openPreview === segment.id && (
											<div style={{ marginTop: 12 }}>
												<ClipVideo src={segmentPreviewUrl(segment.id)} width={420} />
												<p className="sm-meta">
													원본 화면비 그대로입니다. 시작 지점이 몇 초 앞당겨질 수 있습니다.
												</p>
											</div>
										)}
									</article>
								);
							})}

							{/* 구간이 40개가 넘으면 후보도 그만큼 나온다. 위에서부터 몇 개만 보면 대개 결정이
							    끝나므로 나머지는 접어 둔다 — 세로로 40개를 늘어놓으면 아래의 완성 클립이 아예
							    보이지 않는다. */}
							{ranked.length > RANKED_PREVIEW && (
								<button
									type="button"
									className="button button--small"
									onClick={() => setShowAllRanked((on) => !on)}
								>
									{showAllRanked
										? `상위 ${RANKED_PREVIEW}개만 보기`
										: `후보 ${ranked.length - RANKED_PREVIEW}개 더 보기`}
								</button>
							)}

							{excluded.length > 0 && (
								<details className="sm-fold">
									<summary>자립성 관문에서 제외된 구간 {excluded.length}개</summary>
									<ul>
										{excluded.map((entry) => {
											const segment = segments.find((s) => s.idx === entry.idx);
											return (
												<li key={entry.idx} style={{ marginTop: 8 }}>
													{segment && (
														<span className="sm-meta">
															{time(segment.start_sec)}~{time(segment.end_sec)}{" "}
														</span>
													)}
													{segment?.description}
													<div className="sm-meta">{entry.reason}</div>
												</li>
											);
										})}
									</ul>
								</details>
							)}
						</>
					)}
				</details>
			)}
		</>
	);
}
