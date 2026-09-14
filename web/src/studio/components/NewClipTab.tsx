import { Link } from "react-router-dom";
import { renderClip, runCut, runRank, segmentPreviewUrl } from "../api";
import { ClipVideo } from "./ClipVideo";
import { Step } from "./Step";
import { time } from "../../shared/format";
import type { SourceView } from "../SourcePage";

// 후보를 한 번에 몇 개 보여줄까. 구간이 40개면 후보도 40개라 전부 그리면 화면이 그것만으로 찬다.
const RANKED_PREVIEW = 7;

/**
 * 새 클립 탭 — 기준을 직접 적어 구간을 고르고 클립을 만드는 길(5~7단계).
 *
 * 🔴 시청자 질문에 답하는 길(질문 탭)과 **다른 길**이다. 예전에는 "클립" 탭 맨 아래에 접혀 있어서
 * 있는 줄도 모르고 지나쳤다(2026-09-14 사용자 피드백). 만든 결과는 공개 탭에 쌓인다.
 */
export function NewClipTab({ view }: { view: SourceView }) {
	const {
		data,
		geminiReady,
		jobBusy,
		submit,
		segments,
		latestRun,
		ranked,
		excluded,
		clipBySegment,
		nextStep,
		prepDone,
		criteria,
		setCriteria,
		openPreview,
		setOpenPreview,
		showAllRanked,
		setShowAllRanked,
	} = view;

	if (!data) {
		return null;
	}
	const sourceId = data.source.id;

	return (
		<>
			<p className="sm-newclip__lead">
				시청자 질문과 별개로, 기준을 직접 적어 영상에서 구간을 고릅니다. 만든 클립은{" "}
				<Link to={`/sources/${sourceId}/clips`}>공개</Link> 탭에 쌓이고, 거기서 시청자에게 내보냅니다.
			</p>

			{/* 구간이 없으면 고를 대상 자체가 없다. 버튼만 잠겨 있으면 왜 안 눌리는지 알 수 없다. */}
			{!prepDone && (
				<p className="sm-meta sm-hint">
					영상 준비가 끝나야 구간을 고를 수 있습니다 —{" "}
					<Link to={`/sources/${sourceId}/prepare`}>영상 준비</Link> 탭에서 마저 진행하세요.
				</p>
			)}

			<Step
				no={5}
				title="선정"
				done={ranked.length > 0}
				next={nextStep === 5}
				detail={ranked.length > 0 ? `${ranked.length}개 후보` : "기준에 맞는 구간을 고릅니다"}
			>
				{/* 🔴 기준은 한 줄로 끝나지 않는다 — 몇 문장짜리 요청이 흔하다. 제목 옆 한 줄 입력칸에
				    넣었더니 앞부분만 보이고 나머지는 커서를 옮겨 가며 읽어야 했다(2026-09-14). */}
				<div className="sm-criteria">
					<textarea
						className="field__input sm-criteria__input"
						rows={3}
						value={criteria}
						placeholder="클립에 담고 싶은 내용을 작성해 주세요"
						onChange={(e) => setCriteria(e.target.value)}
						aria-label="기준 프롬프트"
					/>
					<div className="sm-criteria__foot">
						<span className="sm-meta">
							비워 두면 기준 없이, 앞뒤 맥락 없이도 이해되는 구간만 고릅니다.
						</span>
						{/* 긴 기준을 조금 고쳐 다시 돌리는 일이 잦다. 처음부터 다시 치게 하지 않는다. */}
						{latestRun?.criteria_prompt && criteria.trim() !== latestRun.criteria_prompt && (
							<button
								type="button"
								className="sm-linkbtn"
								onClick={() => setCriteria(latestRun.criteria_prompt ?? "")}
							>
								지난 기준 불러오기
							</button>
						)}
						<span className="sm-actions sm-actions--end">
							<button
								type="button"
								className={`button button--small${nextStep === 5 ? " sm-go" : ""}`}
								disabled={jobBusy || segments.length === 0 || !geminiReady}
								onClick={() => submit(() => runRank(sourceId, criteria.trim() || null))}
							>
								{ranked.length > 0 ? "다시 선정" : "실행"}
							</button>
						</span>
					</div>
				</div>
			</Step>

			{ranked.length > 0 && (
				<>
					<h3 className="section-title">6·7단계 — 구간을 골라 클립을 만들고 렌더합니다</h3>
					{/* 기준이 길면 제목 줄에 붙여 둘 수 없다 — 제목이 서너 줄로 불어난다. 따로 한 줄로 뺀다.
					    입력칸의 글과 다를 수 있다(고치는 중이면). 아래 후보는 **이 기준**으로 고른 것이다. */}
					{latestRun && (
						<p className="sm-criteria__used">
							<span className="sm-criteria__usedlabel">이 후보들의 기준</span>
							{latestRun.criteria_prompt ?? "없음 — 앞뒤 맥락 없이도 이해되는지만 봤습니다"}
						</p>
					)}
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
									{/* 만든 클립은 공개 탭에 있다. 이름만 적어 두면 어디 가서 봐야 하는지 모른다. */}
									{clip && (
										<Link className="sm-badge sm-badge--link" to={`/sources/${sourceId}/clips`}>
											클립 {clip.id} · {Math.round(clip.end_sec - clip.start_sec)}초
											{clip.rendered && " · 공개 탭에서 보기 →"}
										</Link>
									)}
									<span className="sm-actions sm-actions--end">
										{segment && (
											<button
												type="button"
												className="button button--small"
												onClick={() => setOpenPreview(openPreview === segment.id ? null : segment.id)}
											>
												{openPreview === segment.id ? "미리보기 닫기" : "미리보기"}
											</button>
										)}
										{segment && latestRun && (
											<button
												type="button"
												className={clip ? "button button--small" : "button button--small sm-go"}
												disabled={jobBusy || !geminiReady}
												onClick={() => submit(() => runCut(latestRun.id, segment.id, Boolean(clip)))}
											>
												{clip ? "다시 만들기" : "6. 클립 만들기"}
											</button>
										)}
										{/* 🔴 6단계와 7단계가 탭을 넘나들지 않게 여기에도 둔다. 클립만 만들고 렌더를 안 하면
										    공개 탭에서 재생할 수도 공개할 수도 없다. */}
										{clip && !clip.rendered && (
											<button
												type="button"
												className="button button--small sm-go"
												disabled={jobBusy}
												onClick={() => submit(() => renderClip(clip.id))}
											>
												7. 렌더하기
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
					    끝나므로 나머지는 접어 둔다. */}
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
						<details className="sm-fold" style={{ marginTop: 16 }}>
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
		</>
	);
}
