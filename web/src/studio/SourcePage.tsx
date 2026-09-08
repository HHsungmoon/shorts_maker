import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
	clipUrl,
	createChunk,
	fetchShortsSource,
	fetchStatus,
	patchClipTitle,
	publishClip,
	publishSource,
	fetchUtterances,
	renderClip,
	reviewClip,
	runCut,
	runRank,
	runSegment,
	runStt,
	segmentPreviewUrl,
} from "./api";
import { ClipVideo } from "./components/ClipVideo";
import { ClusterPanel } from "./components/ClusterPanel";
import { Step } from "./components/Step";
import { StudioBanners, StudioHead } from "./components/StudioChrome";
import { STAGE_LABEL, useStudioJob } from "./useStudioJob";
import { useAsync } from "../shared/useAsync";
import type {
	ShortsClip,
	ShortsCost,
	ShortsSegment,
	ShortsSourceDetail,
	ShortsStatus,
	ShortsUtterance,
} from "./types";
import "./source.css";

const DEFAULT_CRITERIA = "한 문장으로 인용할 만한 핵심 논지";

// 후보를 한 번에 몇 개 보여줄까. 구간이 40개면 후보도 40개라 전부 그리면 화면이 그것만으로 찬다.
const RANKED_PREVIEW = 7;

function time(seconds: number): string {
	const total = Math.round(seconds);
	return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * 영상 하나의 작업 화면.
 *
 * 무엇을 작업 중인지는 **URL 이** 정한다(`/sources/:sourceId`). 예전엔 목록과 한 화면이라
 * 로컬 state 로 골랐는데, 그러면 지금 보는 영상을 링크로 줄 수도 북마크할 수도 없었다.
 */
export function SourcePage() {
	const params = useParams();
	// 숫자가 아니면 소스일 수 없다 — 서버에 물어보기 전에 여기서 거른다.
	const raw = params.sourceId ?? "";
	const sourceId = /^\d+$/.test(raw) ? Number(raw) : null;

	const studio = useStudioJob();
	const { jobBusy, reloadToken, reload, submit, act } = studio;

	const [criteria, setCriteria] = useState(DEFAULT_CRITERIA);
	const [openPreview, setOpenPreview] = useState<number | null>(null);
	const [transcript, setTranscript] = useState<ShortsUtterance[] | null>(null);
	const [language, setLanguage] = useState<string | null>(null);
	const [showAllRanked, setShowAllRanked] = useState(false);
	const [titleEdit, setTitleEdit] = useState<number | null>(null);
	const [titleDraft, setTitleDraft] = useState("");
	// 2단계에서 분석할 범위(초). null 이면 아직 손대지 않은 상태라 기본값을 그때그때 파생한다 —
	// 영상 길이는 소스를 읽고 나서야 오기 때문에 상태에 미리 복사해 두면 0 으로 굳는다.
	const [range, setRange] = useState<{ start: number; end: number } | null>(null);
	// 조각이 이미 있는데도 범위를 다시 고르는 중인가("다시 추출" 을 누른 뒤).
	const [rangeOpen, setRangeOpen] = useState(false);

	const status = useAsync<ShortsStatus>(fetchStatus, [reloadToken]);
	const detail = useAsync<ShortsSourceDetail | null>(
		() => (sourceId === null ? Promise.resolve(null) : fetchShortsSource(sourceId)),
		[sourceId, reloadToken],
	);

	// 🔴 훅을 전부 부른 뒤에 돌려보낸다. 위에서 return 하면 렌더마다 훅 개수가 달라진다.
	if (sourceId === null) {
		return <NotFound />;
	}

	const data = detail.data;
	const geminiReady = status.data?.geminiKey === true;
	// 🔴 서버가 이 소스에 잠금을 걸고 긴 작업을 돌리는 중. 이 탭이 띄운 잡(jobBusy)과 다르다 —
	// 다른 탭이나 CLI 가 돌린 것도 여기 잡힌다. 전사 중에 조각을 지우면 그 전사가 외래키 위반으로
	// 죽어서(2026-09-06에 당했다) 소스를 건드리는 버튼은 이 값으로도 잠근다.
	const sourceBusy = data?.busy === true;

	// 🔴 원본이 몇 조각으로 나뉘는지는 사용자의 선택이 아니라 **메모리 상한**이다 — 95분을 한 번에
	// 전사하면 컨테이너 한도를 넘어 죽어서, 서버가 길이를 보고 25분 안팎으로 자른다. 그래서 화면은
	// 조각을 단위로 다루지 않고 전부 소스 하나로 합쳐 보여준다. 조각은 구현 사정일 뿐이다.
	const chunks = data?.chunks ?? [];
	const hasChunks = chunks.length > 0;
	const utterances = chunks.reduce((n, c) => n + c.utteranceCount, 0);
	// 전사가 조각 하나에서 끊길 수 있다. 다 됐을 때만 3단계를 끝난 것으로 본다.
	const sttDone = hasChunks && chunks.every((c) => c.utteranceCount > 0);
	// 준비된 분량. 조각 경계는 겹치지 않으므로 합이 곧 영상 전체 길이다.
	const coveredSec = chunks.reduce((n, c) => n + (c.end_sec - c.start_sec), 0);
	// 초 단위로 다룬다 — duration_sec 은 소수점이 붙어 올 수 있고, 그대로 두면 "0분 0.4초" 가 뜬다.
	const duration = Math.round(data?.source.duration_sec ?? 0);
	// 추출된 범위. 조각 경계는 겹치지 않으므로 처음과 끝만 보면 원래 고른 범위가 나온다.
	const chunkStart = hasChunks ? Math.min(...chunks.map((c) => c.start_sec)) : 0;
	const chunkEnd = hasChunks ? Math.max(...chunks.map((c) => c.end_sec)) : 0;
	// 조각이 없으면 편집이 기본 상태다 — 첫 추출은 범위를 정하는 일 그 자체다.
	const editingRange = !hasChunks || rangeOpen;
	const rangeStart = Math.round(range?.start ?? 0);
	// 끝의 기본값은 영상 전체다. 대부분은 이대로 한 번 누르면 끝난다.
	const rangeEnd = Math.round(range?.end ?? duration);
	// 길이를 아직 모르면(0) 상한을 걸지 않는다 — 모르는 값으로 사용자를 막지 않는다.
	const rangeValid = rangeStart < rangeEnd && (duration === 0 || rangeEnd <= duration);
	// 구간 번호(idx)는 소스 안에서 연속이라(segmentation) 조각을 넘어서도 idx 순이 곧 시간 순이다.
	const segments: ShortsSegment[] = chunks.flatMap((c) => c.segments).sort((a, b) => a.idx - b.idx);
	const latestRun = data?.runs[0] ?? null;
	const ranked = latestRun?.ranked?.ranked ?? [];
	const excluded = latestRun?.ranked?.excluded ?? [];
	const clipBySegment = new Map<number, ShortsClip>();
	for (const clip of data?.clips ?? []) {
		const segment = segments.find((s) => s.id === clip.segment_id);
		if (segment) {
			clipBySegment.set(segment.idx, clip);
		}
	}

	// 클립을 출처별로 묶는다. 질문에서 나온 것은 그 질문끼리, 기준에서 나온 것은 기준 문장끼리.
	// 순서는 clips 가 온 순서(최신 먼저)를 따른다 — 방금 만든 것이 위에 있어야 한다.
	const clipGroups: {
		key: string;
		question: string | null;
		criteria: string | null;
		askedBy: number;
		clips: ShortsClip[];
	}[] = [];
	for (const clip of data?.clips ?? []) {
		const key = clip.question ? `q:${clip.question}` : `c:${clip.criteria_prompt ?? ""}`;
		const found = clipGroups.find((g) => g.key === key);
		if (found) {
			found.clips.push(clip);
		} else {
			clipGroups.push({
				key,
				question: clip.question,
				criteria: clip.criteria_prompt,
				askedBy: clip.asked_by,
				clips: [clip],
			});
		}
	}

	// 다음에 눌러야 할 단계 하나만 강조한다.
	const nextStep = !data ? 1 : !hasChunks ? 2 : !sttDone ? 3 : segments.length === 0 ? 4 : 5;

	return (
		<div className="page">
			{/* 브라우저 뒤로가기로도 돌아가지만, 링크로 열린 화면에는 그 길이 없다. */}
			<Link to="/" className="sm-back">
				← 영상 목록
			</Link>

			<StudioHead
				title={data ? data.source.title : "불러오는 중…"}
				count={
					data && `발화 ${utterances} · 구간 ${segments.length} · 클립 ${data.clips.length}`
				}
			/>

			<StudioBanners status={status} studio={studio} />

			{detail.error && (
				<>
					<p className="state state--error">
						이 영상을 찾을 수 없습니다. ({detail.error.message})
					</p>
					<p className="sm-meta" style={{ textAlign: "center" }}>
						<Link to="/">영상 목록으로 돌아가기</Link>
					</p>
				</>
			)}

			{/* 무엇을 뽑을지 정하려면 원본을 봐야 한다. 자체 <video> 가 아니라 유튜브 임베드다 —
			    여기서의 시청도 실제 유튜브 시청 시간이 된다(tease §8-3). */}
			{data &&
				(data.source.youtube_id ? (
					<div className="sm-player">
						<iframe
							src={`https://www.youtube.com/embed/${data.source.youtube_id}`}
							title={data.source.title}
							allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
							allowFullScreen
						/>
					</div>
				) : (
					<p className="sm-meta">임베드할 수 없는 영상입니다</p>
				))}
			{data?.source.channel && <p className="sm-meta">{data.source.channel}</p>}

			{/* 질문이 맨 위인 건 제품 정의 그대로다(tease §3) — 시청자가 묻고, 크리에이터가 묶고,
			    답하고, 발행한다. 아래 준비 단계는 그 앞에 한 번 해두는 일이라 매일 보는 자리를
			    차지하면 안 된다. */}
			{data && (
				<ClusterPanel
					sourceId={data.source.id}
					published={data.source.published}
					busy={jobBusy}
					reloadToken={reloadToken}
					onJob={submit}
					onChanged={reload}
				/>
			)}

			<h2 className="section-title">
				영상 준비 <span className="page-count">— 영상당 한 번만 하면 됩니다</span>
			</h2>

			<Step
				no={1}
				title="원본"
				done={data !== null}
				next={nextStep === 1}
				detail={
					data ? `${data.source.title} · ${time(data.source.duration_sec ?? 0)}` : "불러오는 중…"
				}
				actions={
					data && (
						<>
							{/* 🔴 임베드 id 가 없으면 시청자가 영상을 못 본다. 발행 자체는 막지 않는다 —
							    질문만 받는 영상이 있을 수 있어서, 경고만 하고 판단은 사람에게 맡긴다. */}
							{!data.source.youtube_id && (
								<span className="sm-meta">임베드 불가 (유튜브 id 없음)</span>
							)}
							<span className="sm-meta">
								{data.source.published ? "시청자에게 공개됨" : "비공개"}
							</span>
							<button
								type="button"
								className="button button--small"
								disabled={jobBusy || data.source.status !== "DONE"}
								onClick={() =>
									act(async () => {
										await publishSource(data.source.id, !data.source.published);
										reload();
									})
								}
							>
								{data.source.published ? "공개 내리기" : "시청자에게 공개"}
							</button>
							{data.source.published && (
								<a
									className="button button--small"
									href={`/watch/${data.source.id}`}
									target="_blank"
									rel="noreferrer"
								>
									시청자 화면 열기
								</a>
							)}
						</>
					)
				}
			/>

			<Step
				no={2}
				title="구간 추출"
				done={hasChunks}
				next={nextStep === 2}
				detail={
					editingRange ? (
						<span className={`sm-range${rangeValid ? "" : " sm-range--invalid"}`}>
							<TimeField
								label="시작"
								seconds={rangeStart}
								onChange={(value) => setRange({ start: value, end: rangeEnd })}
							/>
							<span>~</span>
							<TimeField
								label="끝"
								seconds={rangeEnd}
								onChange={(value) => setRange({ start: rangeStart, end: value })}
							/>
							<span className="sm-meta">
								{duration > 0 ? `영상 전체 ${time(duration)}` : "길이를 아직 모릅니다"}
							</span>
						</span>
					) : (
						`${time(chunkStart)} ~ ${time(chunkEnd)} · 총 ${Math.round(coveredSec / 60)}분 · 분석 준비됨`
					)
				}
				actions={
					data && (
						<>
							{sourceBusy && <span className="sm-meta">처리 중 — 끝나면 다시 누를 수 있습니다</span>}
							{editingRange && !rangeValid && (
								<span className="sm-meta">시작이 끝보다 앞서고, 끝이 영상 길이 안이어야 합니다</span>
							)}
							{editingRange ? (
								<>
									<button
										type="button"
										className={`button button--small${nextStep === 2 ? " sm-go" : ""}`}
										disabled={jobBusy || sourceBusy || !rangeValid}
										onClick={() => {
											setRangeOpen(false);
											// 영상 전체면 범위를 아예 빼고 보낸다 — 화면의 끝값은 초 단위로 반올림한
											// 값이라 실으면 마지막 1초 미만이 잘린다. 비우면 서버가 실제 길이를 쓴다.
											const whole = rangeStart === 0 && rangeEnd === duration;
											submit(() =>
												createChunk(data.source.id, {
													...(whole ? {} : { startSec: rangeStart, endSec: rangeEnd }),
													replace: hasChunks,
												}),
											);
										}}
									>
										추출
									</button>
									{hasChunks && (
										<button
											type="button"
											className="button button--small"
											onClick={() => {
												setRangeOpen(false);
												setRange(null);
											}}
										>
											취소
										</button>
									)}
								</>
							) : (
								/* 누르면 바로 다시 뽑지 않는다. 범위를 먼저 펼쳐 사람이 확인하게 한다 —
								   이 버튼 하나로 전사·구간·클립이 통째로 날아가기 때문이다. */
								<button
									type="button"
									className="button button--small"
									disabled={jobBusy || sourceBusy}
									onClick={() => {
										setRange({ start: chunkStart, end: chunkEnd });
										setRangeOpen(true);
									}}
								>
									다시 추출
								</button>
							)}
						</>
					)
				}
			/>

			{/* 조각 수는 고를 수 있는 값이 아니라 처리 방식이다(메모리 상한). 평소엔 접어 두고,
			    전사가 한 조각에서 멈췄을 때 어디서 멈췄는지 여기서 확인한다. */}
			{hasChunks && (
				<details className="sm-fold sm-chunks">
					<summary>{chunks.length}조각으로 나눠 처리 · 상세</summary>
					<ul>
						{chunks.map((chunk) => (
							<li key={chunk.id}>
								<span className="sm-chunks__idx">#{chunk.idx + 1}</span>
								<span>
									{time(chunk.start_sec)} ~ {time(chunk.end_sec)}
								</span>
								<span className="sm-meta">
									{Math.round((chunk.end_sec - chunk.start_sec) / 60)}분 · 발화 {chunk.utteranceCount}개
								</span>
							</li>
						))}
					</ul>
				</details>
			)}

			<Step
				no={3}
				title="음성 인식"
				done={sttDone}
				next={nextStep === 3}
				detail={utterances > 0 ? `발화 ${utterances}개` : "몇 분 걸립니다"}
				actions={
					data &&
					hasChunks && (
						<>
							<select
								className="field__input"
								style={{ width: 110 }}
								value={language ?? data?.source.language ?? ""}
								onChange={(e) => setLanguage(e.target.value)}
								aria-label="음성 언어"
							>
								{Object.entries(status.data?.languages ?? { ko: "한국어", en: "영어" }).map(
									([code, label]) => (
										<option key={code} value={code}>
											{label}
										</option>
									),
								)}
								<option value="">자동 감지</option>
							</select>
							{sourceBusy && <span className="sm-meta">처리 중 — 끝나면 다시 누를 수 있습니다</span>}
							{/* 전사가 중간에 끊겼으면 force 를 끄고 부른다 — 서버가 끝난 데를 건너뛰고 이어서 간다.
							    여기서 force 를 켜면 이미 몇 분씩 걸려 끝낸 전사를 통째로 다시 돌린다. */}
							<button
								type="button"
								className={`button button--small${nextStep === 3 ? " sm-go" : ""}`}
								disabled={jobBusy || sourceBusy}
								onClick={() =>
									submit(() =>
										runStt(data.source.id, language ?? data.source.language ?? null, sttDone),
									)
								}
							>
								{sttDone ? "다시" : utterances > 0 ? "이어서" : "실행"}
							</button>
						</>
					)
				}
			/>

			<Step
				no={4}
				title="주제 분할"
				done={segments.length > 0}
				next={nextStep === 4}
				detail={segments.length > 0 ? `구간 ${segments.length}개` : "전사를 주제 단위로 나눕니다"}
				actions={
					data && (
						<>
							{sourceBusy && <span className="sm-meta">처리 중 — 끝나면 다시 누를 수 있습니다</span>}
							<button
								type="button"
								className={`button button--small${nextStep === 4 ? " sm-go" : ""}`}
								disabled={jobBusy || sourceBusy || utterances === 0 || !geminiReady}
								onClick={() => submit(() => runSegment(data.source.id))}
							>
								{segments.length > 0 ? "다시" : "실행"}
							</button>
						</>
					)
				}
			/>

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
			    실제로는 선택이다. 기본은 접어 둔다 — 주된 길은 위의 질문 루프다. */}
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

			{data && (
				<>
					<h2 className="section-title">참고</h2>

					<details className="sm-fold">
						<summary>전사 {utterances}발화</summary>
						{hasChunks && (
							<div className="sm-actions" style={{ margin: "12px 0" }}>
								{/* 조각별로 읽어 idx 순으로 잇는다. 발화 타임스탬프는 이미 원본 기준이라
								    이어 붙이는 것만으로 영상 하나의 전사가 된다. */}
								<button
									type="button"
									className="button button--small"
									onClick={() =>
										transcript
											? setTranscript(null)
											: act(async () =>
													setTranscript(
														(await Promise.all(chunks.map((c) => fetchUtterances(c.id)))).flat(),
													),
												)
									}
								>
									{transcript ? "닫기" : "불러오기"}
								</button>
							</div>
						)}
						{transcript && (
							<div className="table-wrap" style={{ maxHeight: 320, overflow: "auto" }}>
								<table className="table">
									<tbody>
										{transcript.map((u) => (
											// idx 는 조각 안에서만 0 부터라 조각을 넘으면 겹친다. 시작 초로 구분한다.
											<tr key={`${u.start_sec}-${u.idx}`}>
												<td style={{ width: 56 }} className="sm-meta">
													{time(u.start_sec)}
												</td>
												<td>{u.text}</td>
											</tr>
										))}
									</tbody>
								</table>
							</div>
						)}
					</details>

					<details className="sm-fold">
						<summary>
							API 비용 (추정) —{" "}
							{data.cost.krw.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}원
						</summary>
						<CostDetail cost={data.cost} />
					</details>

					<details className="sm-fold">
						<summary>단계 기록 {data.stageCalls.length}건</summary>
						<div className="table-wrap" style={{ maxHeight: 280, overflow: "auto" }}>
							<table className="table">
								<thead>
									<tr>
										<th>단계</th>
										<th>모델</th>
										<th>토큰</th>
										<th>소요</th>
									</tr>
								</thead>
								<tbody>
									{data.stageCalls.map((call) => (
										<tr key={call.id}>
											<td>
												{STAGE_LABEL[call.stage] ?? call.stage}
												{call.error && <span className="tag tag--rejected">실패</span>}
											</td>
											<td className="sm-meta">{call.model ?? "-"}</td>
											<td className="sm-meta">
												{call.input_tokens === null
													? "-"
													: `in ${call.input_tokens} / out ${call.output_tokens} / think ${call.thinking_tokens ?? 0}`}
											</td>
											<td className="sm-meta">
												{call.latency_ms === null ? "-" : `${(call.latency_ms / 1000).toFixed(1)}s`}
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</details>
				</>
			)}
		</div>
	);
}

// 분·초를 따로 받는다. mm:ss 한 칸보다 오타가 적고(콜론 빠뜨림), 숫자 필드라 모바일에서
// 숫자 키패드가 뜬다. 상태는 항상 초 하나라서 분·초로 갈랐다 합쳐도 값이 그대로 돌아온다.
function TimeField({
	label,
	seconds,
	onChange,
}: {
	label: string;
	seconds: number;
	onChange: (seconds: number) => void;
}) {
	const min = Math.floor(seconds / 60);
	const sec = seconds % 60;
	// 지우는 도중의 빈 칸은 0 으로 읽는다 — NaN 이 들어가면 그 뒤로 입력이 통째로 멈춘다.
	const read = (raw: string) => Math.max(0, Math.floor(Number(raw) || 0));
	return (
		<span className="sm-range__field">
			<span className="sm-meta">{label}</span>
			<input
				type="number"
				min={0}
				value={min}
				aria-label={`${label} 분`}
				onChange={(e) => onChange(read(e.target.value) * 60 + sec)}
			/>
			<span className="sm-meta">분</span>
			<input
				type="number"
				min={0}
				max={59}
				value={sec}
				aria-label={`${label} 초`}
				onChange={(e) => onChange(min * 60 + Math.min(59, read(e.target.value)))}
			/>
			<span className="sm-meta">초</span>
		</span>
	);
}

// 🔴 추정이다. thinking 토큰은 출력 단가로 과금돼 비용이 여기서 튀므로 따로 보여준다.
function CostDetail({ cost }: { cost: ShortsCost }) {
	return (
		<>
			<div className="table-wrap">
				<table className="table">
					<tbody>
						<tr>
							<td>LLM 호출</td>
							<td>{cost.llmCalls}회</td>
						</tr>
						<tr>
							<td>입력 토큰</td>
							<td>{cost.inputTokens.toLocaleString()}</td>
						</tr>
						<tr>
							<td>출력 토큰</td>
							<td>{cost.outputTokens.toLocaleString()}</td>
						</tr>
						<tr>
							<td>사고(thinking) 토큰</td>
							<td>
								{cost.thinkingTokens.toLocaleString()}{" "}
								<span className="sm-meta">출력 단가로 과금됩니다</span>
							</td>
						</tr>
						<tr>
							<td>합계</td>
							<td>
								<strong>{cost.krw.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}원</strong>{" "}
								<span className="sm-meta">${cost.usd.toFixed(4)}</span>
							</td>
						</tr>
					</tbody>
				</table>
			</div>
			<p className="sm-meta">
				{cost.rate.model} · 입력 ${cost.rate.inputUsdPer1M}/1M · 출력 ${cost.rate.outputUsdPer1M}
				/1M · ₩{cost.rate.usdKrw}/$ 기준
			</p>
		</>
	);
}

/** 주소를 손으로 고쳤거나 지워진 영상이다. 막다른 화면을 만들지 않고 목록으로 되돌린다. */
function NotFound() {
	return (
		<div className="page">
			<p className="state">찾을 수 없습니다.</p>
			<p className="sm-meta" style={{ textAlign: "center" }}>
				<Link to="/">영상 목록으로 돌아가기</Link>
			</p>
		</div>
	);
}
