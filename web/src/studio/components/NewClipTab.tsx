import { useState } from "react";
import { Link } from "react-router-dom";
import { renderClip, runCut, runRank, segmentPreviewUrl } from "../api";
import { ClipVideo } from "./ClipVideo";
import { IdeasPanel } from "./IdeasPanel";
import { Hint } from "../../shared/Hint";
import { Modal } from "../../shared/Modal";
import { time } from "../../shared/format";
import type { ConceptKey } from "../types";
import type { RankedEntry, SourceView } from "../SourcePage";

// 결과를 처음에 몇 개 보여줄까. 구간이 66개면 결과도 그만큼이라 전부 그리면 화면이 그것만으로 찬다.
// 3열 격자의 세 줄이다.
const RANKED_PREVIEW = 9;

// 🔴 "예시로 시작" 은 컨셉마다 다르다(서버 `concepts.py` 의 examples, tease §5-11). 처음 쓰는 사람이 빈칸 앞에서
// 멈추지 않게 하는 출발점이라 강연이면 어디에나 맞는 것만 둔다 — 특정 영상의 내용을 적으면 다른 영상에서는 엉뚱하다.

type Filter = "all" | "clip" | "excluded";

/**
 * 새 클립 탭 — 기준을 직접 적어 구간을 고르고 클립을 만드는 길(5~7단계).
 *
 * 🔴 시청자 질문에 답하는 길(질문 탭)과 **다른 길**이다. 예전에는 "클립" 탭 맨 아래에 접혀 있어서
 * 있는 줄도 모르고 지나쳤다(2026-09-14 사용자 피드백). 만든 결과는 공개 탭에 쌓인다.
 *
 * 위는 기준을 쓰는 자리(개선안 D1), 아래는 마지막 선정 결과의 점수순 카드(D3)다(2026-10-24).
 * 결과가 이 탭에 남아 있어서, 카드를 하나씩 클립으로 만들며 내려가면 된다 — 예전에는 결과를 보려고
 * 실행 버튼 아래 긴 목록을 훑어야 했다.
 *
 * 2026-11-08(tease §5-11): 관리자가 만드는 숏폼은 컨셉이 셋이다 — 브랜딩 · 바이럴 · 정보성. 위쪽을 반으로 가른다.
 * 왼쪽은 컨셉 · 기준을 쓰는 자리, 오른쪽은 AI 추천(영상을 읽고 컨셉마다 적합도와 주제). 추천의 주제를 누르면 왼쪽
 * 칸에 들어가고, 사람이 [구간 고르기] 를 누른다. 고른 컨셉은 run 에 남아 자르기 · AI 카피까지 따라간다.
 */
export function NewClipTab({ view }: { view: SourceView }) {
	const {
		data,
		geminiReady,
		jobBusy,
		submit,
		segments,
		criteriaRuns,
		latestRun,
		ranked,
		excluded,
		clipBySegment,
		prepDone,
		duration,
		criteria,
		setCriteria,
		concept,
		setConcept,
		openPreview,
		setOpenPreview,
		showAllRanked,
		setShowAllRanked,
	} = view;
	const [filter, setFilter] = useState<Filter>("all");

	if (!data) {
		return null;
	}
	const sourceId = data.source.id;
	const canRun = !jobBusy && segments.length > 0 && geminiReady;
	const conceptInfo = data.concepts.find((c) => c.key === concept) ?? data.concepts[0];
	const labelOf = (key: ConceptKey | null) => data.concepts.find((c) => c.key === key)?.label ?? null;
	// 지난 기준 — 같은 컨셉 · 같은 글은 한 번만, 최근 것부터 다섯 개. 컨셉 이전의 run 은 컨셉 없이 남는다.
	const history: { concept: ConceptKey | null; text: string }[] = [];
	for (const run of criteriaRuns) {
		const text = run.criteria_prompt ?? "";
		if (text && !history.some((h) => h.text === text && h.concept === run.concept) && history.length < 5) {
			history.push({ concept: run.concept, text });
		}
	}
	const usedLabel = latestRun ? labelOf(latestRun.concept) : null;
	const withClip = ranked.filter((entry) => clipBySegment.has(entry.idx));
	const shown = filter === "clip" ? withClip : ranked;
	const previewSegment = segments.find((s) => s.id === openPreview) ?? null;

	return (
		<div className="sm-make">
			{/* 위쪽 반반 — 왼쪽 기준 쓰기(D1) · 오른쪽 AI 추천(tease §5-11) ---------------------------- */}
			<div className="sm-make__top">
				<section className="sm-make__compose">
					<div className="sm-make__intro">
						<h2 className="sm-make__title">어떤 클립을 뽑을까요?</h2>
						<p className="sm-make__lead">
							시청자 질문과 별개로 관리자가 직접 만드는 숏폼입니다. 만든 클립은{" "}
							<Link to={`/studio/sources/${sourceId}/clips`}>공개</Link> 탭에 쌓입니다.
						</p>
					</div>

					{/* 구간이 없으면 고를 대상 자체가 없다. 버튼만 잠겨 있으면 왜 안 눌리는지 알 수 없다. */}
					{!prepDone && (
						<p className="sm-make__need">
							영상 준비가 끝나야 구간을 고를 수 있습니다 —{" "}
							<Link to={`/studio/sources/${sourceId}/prepare`}>영상 준비</Link> 탭에서 마저 진행하세요.
						</p>
					)}

					<div className="sm-make__box">
						{/* 컨셉 — 무엇을 노리는 숏폼인가. 고르기 · 자르기 · AI 카피가 같은 컨셉을 읽는다. */}
						<div className="sm-make__conceptrow">
							<span className="sm-make__label" id="make-concept">
								컨셉
							</span>
							<span className="button-group" role="group" aria-labelledby="make-concept">
								{data.concepts.map((c) => (
									<button
										key={c.key}
										type="button"
										aria-pressed={c.key === concept}
										className={`button button--small${c.key === concept ? " sm-go" : ""}`}
										onClick={() => setConcept(c.key)}
									>
										{c.label}
									</button>
								))}
							</span>
							{conceptInfo && (
								<Hint label="이 컨셉이 보는 것" align="end">
									<span className="sm-make__conceptgoal">{conceptInfo.goal}</span>
								</Hint>
							)}
						</div>
						{conceptInfo && <p className="sm-make__conceptsum">{conceptInfo.summary}</p>}

						{/* 🔴 기준은 한 줄로 끝나지 않는다 — 몇 문장짜리 요청이 흔하다. 한 줄 입력칸에 넣었더니
						    앞부분만 보이고 나머지는 커서를 옮겨 가며 읽어야 했다(2026-09-14). */}
						<label className="sm-make__label" htmlFor="make-criteria">
							주제 · 기준
						</label>
						<textarea
							id="make-criteria"
							className="sm-make__input"
							rows={3}
							value={criteria}
							placeholder="클립에 담고 싶은 주제를 적어 주세요"
							onChange={(e) => setCriteria(e.target.value)}
						/>
						<div className="sm-make__foot">
							<span className="sm-meta">
								비워 두면 {conceptInfo?.label ?? ""} 컨셉에 맞고 앞뒤 맥락 없이도 이해되는 구간을 고릅니다
							</span>
							<button
								type="button"
								className="button sm-go sm-make__run"
								disabled={!canRun}
								title={!geminiReady ? "GEMINI_API_KEY 가 없어 실행할 수 없습니다" : undefined}
								onClick={() => submit(() => runRank(sourceId, criteria.trim() || null, concept))}
							>
								{ranked.length > 0 ? "다시 고르기" : "구간 고르기"}
							</button>
						</div>
					</div>

					<div className="sm-make__picks">
						{conceptInfo && (
							<div className="sm-make__pickrow">
								<span className="sm-make__picklabel">예시로 시작</span>
								{conceptInfo.examples.map((text) => (
									<button key={text} type="button" className="sm-make__chip" onClick={() => setCriteria(text)}>
										{text}
									</button>
								))}
							</div>
						)}
						{/* 긴 기준을 조금 고쳐 다시 돌리는 일이 잦다. 처음부터 다시 치게 하지 않는다. 컨셉도 함께 돌아온다. */}
						{history.length > 0 && (
							<div className="sm-make__pickrow">
								<span className="sm-make__picklabel">지난 기준</span>
								{history.map((entry) => (
									<button
										key={`${entry.concept}-${entry.text}`}
										type="button"
										className="sm-make__chip sm-make__chip--past"
										title={entry.text}
										onClick={() => {
											if (entry.concept) {
												setConcept(entry.concept);
											}
											setCriteria(entry.text);
										}}
									>
										{entry.concept && <span className="sm-make__chiptag">{labelOf(entry.concept)}</span>}
										{entry.text}
									</button>
								))}
							</div>
						)}
					</div>
				</section>

				<IdeasPanel view={view} onPick={() => document.getElementById("make-criteria")?.focus()} />
			</div>

			{/* 결과(D3) ----------------------------------------------------------------- */}
			{latestRun && (
				<section className="sm-make__results" aria-label="선정 결과">
					<div className="sm-make__rhead">
						<h3 className="sm-make__rtitle">결과 {ranked.length}개</h3>
						<span className="sm-meta">
							점수순 · {new Date(latestRun.created_at).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" })}
						</span>
						<div className="sm-make__filters" role="tablist">
							<FilterChip on={filter === "all"} onClick={() => setFilter("all")} label="전체" count={ranked.length} />
							<FilterChip on={filter === "clip"} onClick={() => setFilter("clip")} label="클립 만듦" count={withClip.length} />
							<FilterChip
								on={filter === "excluded"}
								onClick={() => setFilter("excluded")}
								label="제외됨"
								count={excluded.length}
							/>
						</div>
					</div>
					{/* 기준이 길면 제목 줄에 붙여 둘 수 없다. 입력칸의 글과 다를 수 있다(고치는 중이면) —
					    아래 카드는 **이 기준**으로 고른 것이다. */}
					<p className="sm-make__used">
						<span className="sm-make__usedlabel">
							이 결과의 기준{usedLabel ? ` · 컨셉 ${usedLabel}` : " · 컨셉 없음(컨셉을 넣기 전의 결과)"}
						</span>
						{latestRun.criteria_prompt ??
							(usedLabel
								? `없음 — ${usedLabel} 컨셉에 맞는지와 앞뒤 맥락 없이도 이해되는지를 봤습니다`
								: "없음 — 앞뒤 맥락 없이도 이해되는지만 봤습니다")}
					</p>
					{latestRun.error && <p className="state state--error">{latestRun.error}</p>}

					{filter === "excluded" ? (
						excluded.length === 0 ? (
							<p className="sm-make__empty">제외된 구간이 없습니다.</p>
						) : (
							<div className="sm-make__grid">
								{excluded.map((entry) => {
									const segment = segments.find((s) => s.idx === entry.idx);
									return (
										<article key={entry.idx} className="sm-card sm-card--out">
											<div className="sm-card__top">
												<span className="sm-card__label">제외</span>
												{segment && (
													<span className="sm-meta">
														#{segment.idx + 1} · {time(segment.start_sec)}
													</span>
												)}
											</div>
											{segment?.description && <p className="sm-card__desc">{segment.description}</p>}
											<p className="sm-card__reason">{entry.reason}</p>
										</article>
									);
								})}
							</div>
						)
					) : shown.length === 0 ? (
						<p className="sm-make__empty">
							{filter === "clip" ? "아직 이 결과로 만든 클립이 없습니다." : "고른 구간이 없습니다."}
						</p>
					) : (
						<>
							<div className="sm-make__grid">
								{shown.slice(0, showAllRanked ? undefined : RANKED_PREVIEW).map((entry) => (
									<ResultCard
										key={entry.idx}
										entry={entry}
										rank={ranked.indexOf(entry) + 1}
										view={view}
										duration={duration}
										runId={latestRun.id}
									/>
								))}
							</div>
							{shown.length > RANKED_PREVIEW && (
								<button
									type="button"
									className="button sm-make__more"
									onClick={() => setShowAllRanked((on) => !on)}
								>
									{showAllRanked ? `위 ${RANKED_PREVIEW}개만 보기` : `${shown.length - RANKED_PREVIEW}개 더 보기`}
								</button>
							)}
						</>
					)}
				</section>
			)}

			{/* 미리보기는 카드 안에 펼치지 않고 모달로 띄운다 — 3열 격자 안에서 펼치면 그 줄만 길어져 격자가 깨진다. */}
			<Modal
				open={previewSegment !== null}
				title={previewSegment ? `#${previewSegment.idx + 1} · ${time(previewSegment.start_sec)}–${time(previewSegment.end_sec)}` : ""}
				subhead={previewSegment?.description ?? undefined}
				onClose={() => setOpenPreview(null)}
			>
				{previewSegment && (
					<div className="sm-make__preview">
						<ClipVideo src={segmentPreviewUrl(previewSegment.id)} width={560} />
						<p className="sm-meta">원본 화면비 그대로입니다. 시작 지점이 몇 초 앞당겨질 수 있습니다.</p>
					</div>
				)}
			</Modal>
		</div>
	);
}

function FilterChip({ on, onClick, label, count }: { on: boolean; onClick: () => void; label: string; count: number }) {
	return (
		<button type="button" role="tab" aria-selected={on} className={`sm-chip${on ? " sm-chip--on" : ""}`} onClick={onClick}>
			{label} <span className="sm-chip__n">{count}</span>
		</button>
	);
}

/** 결과 카드 하나 — 점수 · 원본 위치 · 구간 설명 · 고른 이유 · 만들기. */
function ResultCard({
	entry,
	rank,
	view,
	duration,
	runId,
}: {
	entry: RankedEntry;
	rank: number;
	view: SourceView;
	duration: number;
	runId: number;
}) {
	const { segments, clipBySegment, jobBusy, geminiReady, submit, setOpenPreview, sourceId } = view;
	const segment = segments.find((s) => s.idx === entry.idx);
	const clip = clipBySegment.get(entry.idx);
	const at = (sec: number) => `${Math.min(100, Math.max(0, (sec / duration) * 100))}%`;
	return (
		<article className={`sm-card${rank === 1 ? " sm-card--top" : ""}${clip ? " sm-card--made" : ""}`}>
			<div className="sm-card__top">
				<span className="sm-card__rank">{rank}</span>
				<span className="sm-card__score">{entry.score}</span>
				<span className="sm-meta">점</span>
				{segment && (
					<span className="sm-card__where">
						#{segment.idx + 1} · {time(segment.start_sec)} · {Math.round((segment.end_sec - segment.start_sec) / 60)}분
					</span>
				)}
			</div>
			{/* 원본 어디쯤인가. 질문 탭의 원본 막대와 같은 모양이다. */}
			{segment && duration > 0 && (
				<div className="sm-card__pos" aria-hidden="true">
					<span style={{ left: at(segment.start_sec), width: at(segment.end_sec - segment.start_sec) }} />
				</div>
			)}
			{segment?.description && <p className="sm-card__desc">{segment.description}</p>}
			<p className="sm-card__reason">{entry.reason}</p>

			<div className="sm-card__actions">
				{segment && (
					<button type="button" className="button button--small" onClick={() => setOpenPreview(segment.id)}>
						미리보기
					</button>
				)}
				{segment && (
					<button
						type="button"
						className={`button button--small${clip ? "" : " sm-go"}`}
						disabled={jobBusy || !geminiReady}
						onClick={() => submit(() => runCut(runId, segment.id, Boolean(clip)))}
					>
						{clip ? "다시 만들기" : "클립 만들기"}
					</button>
				)}
				{/* 🔴 만들기와 렌더가 탭을 넘나들지 않게 여기에도 둔다. 렌더를 안 하면 공개 탭에서 재생할 수도
				    공개할 수도 없다. */}
				{clip && !clip.rendered && (
					<button
						type="button"
						className="button button--small sm-go"
						disabled={jobBusy}
						onClick={() => submit(() => renderClip(clip.id))}
					>
						렌더하기
					</button>
				)}
				{/* 만든 클립은 공개 탭에 있다. 이름만 적어 두면 어디 가서 봐야 하는지 모른다. */}
				{clip?.rendered && (
					<Link className="sm-card__link" to={`/studio/sources/${sourceId}/clips`}>
						공개 탭에 있음 →
					</Link>
				)}
			</div>
		</article>
	);
}
