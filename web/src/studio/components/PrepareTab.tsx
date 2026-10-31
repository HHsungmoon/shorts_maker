import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
	createChunk,
	draftOverview,
	fetchUtterances,
	patchSource,
	publishSource,
	importCaptions,
	runSegment,
	runStt,
} from "../api";
import { time } from "../../shared/format";
import { Modal } from "../../shared/Modal";
import type { SourceView } from "../SourcePage";
import type { ShortsUtterance, SttFix, TranscriptOrigin } from "../types";
import { TranscriptCompare } from "./TranscriptCompare";

const ORIGIN_LABEL: Record<TranscriptOrigin, string> = { youtube: "유튜브 자막", whisper: "음성 인식" };

/** 위 단계 표시가 데려가는 자리. 단계마다 아래에 같은 id 의 칸이 있다. */
const STEP_IDS = ["prep-source", "prep-range", "prep-stt", "prep-segments"] as const;

/**
 * 영상 준비 탭 — 1~4단계. 영상당 한 번 하고 다시 볼 일이 없어서 매일 쓰는 질문 탭과
 * 자리를 나눴다.
 *
 * 🔴 **한 페이지에 위에서 아래로** 놓는다(개선안 C1·C2·C3 종합, 2026-10-24). 맨 위 단계 표시를
 * 누르면 그 단계 칸으로 **내려간다** — 눌러서 내용이 바뀌는 탭으로 만들면 "다른 단계는 어떻게
 * 됐지" 를 보려고 계속 눌러 봐야 하고, 끝난 영상은 결국 아래 구간 타임라인만 본다.
 *   - 단계 표시(C1): 넷이 한 줄. 끝난 것·지금 할 것이 한눈에 보인다
 *   - 원본(C2): 왼쪽 이름 · 오른쪽 값의 설정 줄. 시청자 공개는 켜고 끄는 스위치다
 *   - 주제 분할(C3): 원본 전체 길이 위에 구간을 그리고, 고른 구간의 설명과 전사를 아래에 펼친다
 */
export function PrepareTab({ view }: { view: SourceView }) {
	const {
		data,
		status,
		geminiReady,
		sourceBusy,
		jobBusy,
		submit,
		act,
		reload,
		chunks,
		hasChunks,
		utterances,
		sttDone,
		duration,
		chunkStart,
		chunkEnd,
		segments,
		clipBySegment,
		nextStep,
		editingRange,
		rangeStart,
		rangeEnd,
		rangeValid,
		setRange,
		setRangeOpen,
		language,
		setLanguage,
		job,
		playAt,
	} = view;

	// 개요 초안은 DB 가 아니라 **잡 결과**로 온다 — 저장하지 않고 돌려주기 때문이다.
	// 🔴 다른 잡의 결과를 개요 칸에 붓지 않도록 kind 까지 본다.
	const overviewDraft =
		job !== null && job.kind === "overview" && job.status === "DONE"
			? ((job.result as { context?: string } | null)?.context ?? null)
			: null;

	const done = [data !== null, hasChunks, sttDone, segments.length > 0];
	const doneCount = done.filter(Boolean).length;
	const steps = [
		{ title: "원본", detail: data ? time(data.source.duration_sec ?? 0) : "불러오는 중…" },
		{ title: "구간 추출", detail: hasChunks ? `${chunks.length}조각` : "범위를 정합니다" },
		{ title: "대사 가져오기", detail: utterances > 0 ? `발화 ${utterances.toLocaleString()}` : "유튜브 자막은 몇 초" },
		{ title: "주제 분할", detail: segments.length > 0 ? `구간 ${segments.length}` : "전사를 주제로 나눕니다" },
	];

	// 마지막으로 성공한 음성 인식 호출. 무엇으로 돌렸는지(모델·언어·용어 힌트)가 여기 남아 있다.
	// stageCalls 는 최근 것이 앞이다.
	const lastStt = data?.stageCalls.find((c) => c.stage === "stt" && c.error === null) ?? null;
	// 지금 쓰는 대사가 어디서 왔나(tease §5-1). 유튜브 자막이 기본이고, 음성 인식은 자막이 없을 때 누른다.
	const captionSource = data?.source.caption_source ?? null;
	// 🔴 음성 인식은 같은 오디오에 한 번뿐이다 — 들은 것이 있고 남은 것이 없으면 끝났다.
	const sttFinished = !!data && data.stt.heardSec > 0 && data.stt.remainingSec === 0;
	const sttRunning = jobBusy && job?.kind === "stt";
	const [confirmStt, setConfirmStt] = useState(false);
	const maxPerMin = Math.max(
		0,
		...chunks.map((c) => c.utteranceCount / Math.max(1, (c.end_sec - c.start_sec) / 60)),
	);

	function goTo(index: number) {
		document.getElementById(STEP_IDS[index])?.scrollIntoView({ behavior: "smooth", block: "start" });
	}

	return (
		<div className="sm-prep">
			<div className="sm-prep__head">
				<h2 className="sm-prep__title">영상 준비</h2>
				<span className="sm-meta">
					영상당 한 번 · 4단계 중 {doneCount}단계 끝남
					{doneCount === 4 && " — 질문에 답할 수 있습니다"}
				</span>
			</div>

			{/* 단계 표시(C1). 누르면 아래 그 단계 칸으로 내려간다. */}
			<nav className="sm-steps" aria-label="준비 단계">
				{steps.map((step, index) => {
					const isDone = done[index];
					const isNext = nextStep === index + 1;
					return (
						<button
							key={step.title}
							type="button"
							className={`sm-steps__item${isDone ? " sm-steps__item--done" : ""}${isNext ? " sm-steps__item--next" : ""}`}
							onClick={() => goTo(index)}
						>
							<span className="sm-steps__no" aria-hidden="true">
								{isDone ? "✓" : index + 1}
							</span>
							<span className="sm-steps__text">
								<span className="sm-steps__title">{step.title}</span>
								<span className="sm-steps__detail">{isNext ? "지금 할 차례" : step.detail}</span>
							</span>
						</button>
					);
				})}
			</nav>

			{/* 원본 | 구간 추출·음성 인식 — 두 칸이 나란히, 주제 분할은 그 아래 폭 전체(2026-10-24).
			    구간 추출과 음성 인식은 한 번 돌고 나면 볼 것이 적어서 한 칸에 모았다. 주제 분할이 이 탭의 본체다. */}
			<div className="sm-prep__top">
			{/* 1 · 원본 (C2) ------------------------------------------------------------ */}
			<section id={STEP_IDS[0]} className="sm-prep__card">
				<StepHead no={1} title="원본" done={done[0]} next={nextStep === 1}>
					{data && `${time(data.source.duration_sec ?? 0)} · ${data.source.channel ?? "채널 모름"}`}
				</StepHead>
				{data && (
					<>
						<div className="sm-prep__row">
							<div className="sm-prep__label">
								시청자 공개
								<span className="sm-meta">끄면 시청자 화면에서 사라집니다</span>
							</div>
							<div className="sm-prep__value sm-actions">
								<button
									type="button"
									role="switch"
									aria-checked={data.source.published}
									aria-label="시청자 공개"
									className={`sm-switch${data.source.published ? " sm-switch--on" : ""}`}
									disabled={jobBusy || data.source.status !== "DONE"}
									onClick={() =>
										act(async () => {
											await publishSource(data.source.id, !data.source.published);
											reload();
										})
									}
								>
									<span className="sm-switch__knob" />
								</button>
								<span>{data.source.published ? "공개 중" : "비공개"}</span>
								{/* 🔴 임베드 id 가 없으면 시청자가 영상을 못 본다. 발행 자체는 막지 않는다 —
								    질문만 받는 영상이 있을 수 있어서, 경고만 하고 판단은 사람에게 맡긴다. */}
								{!data.source.youtube_id && (
									<span className="sm-prep__warn">유튜브 id 가 없어 시청자가 원본을 못 봅니다</span>
								)}
								{data.source.published && (
									<a
										className="sm-actions--end"
										href={`/watch/${data.source.id}`}
										target="_blank"
										rel="noreferrer"
									>
										시청자 화면 열기 ↗
									</a>
								)}
							</div>
						</div>
						<SourceInfoEditor
							key={data.source.id}
							sourceId={data.source.id}
							savedTitle={data.source.title}
							savedYoutubeId={data.source.youtube_id}
							savedContext={data.source.context}
							disabled={jobBusy}
							onSaved={reload}
							// 구간 요약이 있어야 개요를 쓸 재료가 있다(pipeline/overview.py 머리 주석).
							canDraft={segments.length > 0}
							onDraft={() => submit(() => draftOverview(data.source.id))}
							draftedContext={overviewDraft}
						/>
					</>
				)}
			</section>

			{/* 2·3 · 구간 추출 · 대사 가져오기 ------------------------------------------ */}
			<section id={STEP_IDS[1]} className="sm-prep__card">
				<StepHead no={2} title="구간 추출" done={done[1]} next={nextStep === 2}>
					{hasChunks && !editingRange
						? `${time(chunkStart)} ~ ${time(chunkEnd)} · ${chunks.length}조각`
						: "분석할 범위를 정합니다"}
					{data && hasChunks && !editingRange && (
						/* 누르면 바로 다시 뽑지 않는다. 범위를 먼저 펼쳐 사람이 확인하게 한다 —
						   이 버튼 하나로 전사·구간·클립이 통째로 날아가기 때문이다. */
						<button
							type="button"
							className="button button--small sm-actions--end"
							disabled={jobBusy || sourceBusy}
							onClick={() => {
								setRange({ start: chunkStart, end: chunkEnd });
								setRangeOpen(true);
							}}
						>
							다시 추출
						</button>
					)}
				</StepHead>
				{data && editingRange && (
					<div className="sm-prep__body sm-actions">
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
						{!rangeValid && (
							<span className="sm-meta">시작이 끝보다 앞서고, 끝이 영상 길이 안이어야 합니다</span>
						)}
						<span className="sm-actions sm-actions--end">
							{sourceBusy && <span className="sm-meta">처리 중 — 끝나면 다시 누를 수 있습니다</span>}
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
						</span>
					</div>
				)}

				{hasChunks && !editingRange && (
					<RangeOverview
						chunks={chunks}
						duration={duration > 0 ? duration : chunkEnd}
						calls={data?.stageCalls ?? []}
					/>
				)}

				{/* 3 · 대사 가져오기 — 🔴 유튜브 자막이 기본, 음성 인식은 두 번째 버튼(2026-11-01, tease §5-1).
				    whisper 가 없는 문장을 지어내는 것을 실측했다. 두 대사는 따로 보관되고(transcripts) 쓰는 것 하나를
				    고른다 — 자막을 받아도 음성 인식이 날아가지 않고, 둘이 다 있으면 아래 비교 칸이 열린다.
				    🔴 음성 인식은 같은 오디오에 한 번뿐이다. 다 들었으면 버튼 대신 "끝남" 만 보인다. */}
				<div id={STEP_IDS[2]} className="sm-prep__sub">
					<StepHead no={3} title="대사 가져오기" done={done[2]} next={nextStep === 3}>
						{utterances > 0
							? `발화 ${utterances.toLocaleString()} · ${captionSource ? ORIGIN_LABEL[captionSource] : "출처 모름"}`
							: "유튜브 자막은 몇 초 · 음성 인식은 길이의 1.4배쯤"}
						{data && hasChunks && (
							<span className="sm-actions sm-actions--end">
								{/* 유튜브 id 가 없으면 받을 곳이 없다 — 이건 권한이 아니라 전제라 잠그고 이유를 옆에 쓴다. */}
								{!data.source.youtube_id && <span className="sm-meta">유튜브 id 가 없어 음성 인식만 됩니다</span>}
								<button
									type="button"
									className={`button button--small${nextStep === 3 && data.source.youtube_id ? " sm-go" : ""}`}
									disabled={jobBusy || sourceBusy || !data.source.youtube_id}
									onClick={() => submit(() => importCaptions(data.source.id))}
								>
									{data.transcripts.youtube > 0 ? "자막 다시 가져오기" : "유튜브 자막"}
								</button>
								{sttRunning ? (
									<button type="button" className="button button--small" disabled>
										음성 인식 중…
									</button>
								) : sttFinished ? (
									<span className="sm-meta" title="같은 오디오는 다시 듣지 않습니다">
										음성 인식 끝남 · {time(data.stt.heardSec)} 들음
									</span>
								) : (
									<button
										type="button"
										className={`button button--small${nextStep === 3 && !data.source.youtube_id ? " sm-go" : ""}`}
										disabled={jobBusy || sourceBusy}
										onClick={() => setConfirmStt(true)}
									>
										{data.stt.heardSec > 0 ? "음성 인식 이어서" : "음성 인식"}
									</button>
								)}
							</span>
						)}
					</StepHead>
					{/* 🔴 되돌릴 수 없는 한 번이라 시작 전에 한 번 더 보여 준다 — 언어를 잘못 고르면 다시 돌릴 길이 없다.
					    모달이 아니라 이 자리에 펼친다(누른 버튼 바로 아래라 눈에 들어온다). */}
					{data && confirmStt && !sttFinished && !sttRunning && (
						<div className="sm-prep__confirm">
							<p>
								음성 인식은 <strong>같은 오디오에 한 번만</strong> 합니다 — 끝나면 다시 돌릴 수 없습니다.
								<br />
								<span className="sm-meta">
									남은 범위 {time(data.stt.remainingSec)} · 약 {Math.max(1, Math.round((data.stt.remainingSec * 1.4) / 60))}분
									걸립니다(실측 0.7배속). 도는 동안 다른 작업은 기다립니다.
									{data.transcripts.youtube > 0 && " 지금 쓰는 자막 대사는 그대로이고, 끝나면 둘을 비교합니다."}
								</span>
							</p>
							<span className="sm-actions">
								<select
									className="field__input sm-prep__lang"
									value={language ?? data.source.language ?? ""}
									onChange={(e) => setLanguage(e.target.value)}
									aria-label="음성 언어"
								>
									{Object.entries(status.data?.languages ?? { ko: "한국어", en: "영어" }).map(([code, label]) => (
										<option key={code} value={code}>
											{label}
										</option>
									))}
									<option value="">자동 감지</option>
								</select>
								<button
									type="button"
									className="button button--small sm-go"
									disabled={jobBusy || sourceBusy}
									onClick={() => {
										setConfirmStt(false);
										submit(() => runStt(data.source.id, language ?? data.source.language ?? null));
									}}
								>
									시작
								</button>
								<button type="button" className="button button--small" onClick={() => setConfirmStt(false)}>
									취소
								</button>
							</span>
						</div>
					)}
				</div>

				{data && (
					<SttSettings
						call={lastStt}
						languages={status.data?.languages ?? {}}
						terms={data.source.stt_terms ?? []}
						fixes={data.source.stt_fixes ?? []}
						transcribed={utterances > 0}
						onSave={(terms) =>
							act(async () => {
								await patchSource(data.source.id, { sttTerms: terms });
								reload();
							})
						}
						onSaveFixes={(fixes, fixed) =>
							act(async () => {
								const saved = await patchSource(data.source.id, { sttFixes: fixes });
								fixed(saved.fixedUtterances ?? 0);
								reload();
							})
						}
					/>
				)}

				{/* 조각별 말의 밀도. 처리 조각은 메모리 상한 때문에 나눈 것이지만, 나눠 놓고 보면 어느 시간대에
				    말이 몰렸는지(분당 발화)가 보인다 — 질의응답처럼 짧은 말이 오가는 시간대가 튄다. 전사가 한
				    조각에서 멈췄으면 그 조각만 "대기" 로 남아 어디서 멈췄는지도 여기서 보인다. */}
				{hasChunks && (
					<ul className="sm-prep__chunks" aria-label="조각별 발화">
						{chunks.map((chunk) => {
							const minutes = Math.max(1, (chunk.end_sec - chunk.start_sec) / 60);
							const perMin = chunk.utteranceCount / minutes;
							return (
								<li key={chunk.id}>
									<span className="sm-prep__chunktime">
										{time(chunk.start_sec)}–{time(chunk.end_sec)}
									</span>
									<span className="sm-prep__chunkbar">
										<span style={{ width: maxPerMin > 0 ? `${(perMin / maxPerMin) * 100}%` : 0 }} />
									</span>
									<span className="sm-prep__chunkstate">
										{chunk.utteranceCount > 0
											? `${chunk.utteranceCount.toLocaleString()} · 분당 ${perMin.toFixed(0)}`
											: "대기"}
									</span>
								</li>
							);
						})}
					</ul>
				)}
			</section>
			</div>

			{/* 자막 ↔ 음성 인식 비교 — 둘 다 있을 때만(tease §5-1). 폭 전체를 쓴다: 두 줄을 나란히 읽어야 한다. */}
			{data && data.transcripts.youtube > 0 && data.transcripts.whisper > 0 && (
				<section className="sm-prep__card">
					<div className="sm-prep__stephead">
						<h3 className="sm-prep__steptitle">자막 ↔ 음성 인식 비교</h3>
						<span className="sm-prep__stepdetail">어느 쪽이 맞는지는 들어 봐야 압니다 — 시각을 누르면 그 자리부터 틉니다</span>
					</div>
					<TranscriptCompare
						sourceId={data.source.id}
						using={captionSource}
						version={`${data.transcripts.youtube}-${data.transcripts.whisper}-${data.stt.heardSec}-${JSON.stringify(data.source.stt_fixes ?? [])}`}
						busy={jobBusy || sourceBusy}
						act={act}
						playAt={playAt}
					/>
				</section>
			)}

			{/* 4 · 주제 분할 (C3) ------------------------------------------------------- */}
			<section id={STEP_IDS[3]} className="sm-prep__card">
				<StepHead no={4} title="주제 분할" done={done[3]} next={nextStep === 4}>
					{segments.length > 0 ? `구간 ${segments.length}개` : "전사를 주제 단위로 나눕니다"}
					{data && (
						<span className="sm-actions sm-actions--end">
							{sourceBusy && <span className="sm-meta">처리 중</span>}
							<button
								type="button"
								className={`button button--small${nextStep === 4 ? " sm-go" : ""}`}
								disabled={jobBusy || sourceBusy || utterances === 0 || !geminiReady}
								onClick={() => submit(() => runSegment(data.source.id))}
							>
								{segments.length > 0 ? "다시 분할" : "실행"}
							</button>
						</span>
					)}
				</StepHead>
				{segments.length > 0 && (
					<SegmentTimeline
						segments={segments}
						chunks={chunks}
						duration={duration > 0 ? duration : chunkEnd}
						clipSegments={new Set(clipBySegment.keys())}
					/>
				)}
			</section>
		</div>
	);
}

/** 단계 칸의 머리. 번호(또는 ✓) · 이름 · 한 줄 요약 · 오른쪽 끝 동작. */
function StepHead({
	no,
	title,
	done,
	next,
	children,
}: {
	no: number;
	title: string;
	done: boolean;
	next: boolean;
	children?: ReactNode;
}) {
	return (
		<div className={`sm-prep__stephead${next ? " sm-prep__stephead--next" : ""}`}>
			<span className={`sm-steps__no${done ? " sm-steps__no--done" : ""}`} aria-hidden="true">
				{done ? "✓" : no}
			</span>
			<h3 className="sm-prep__steptitle">{title}</h3>
			<span className="sm-prep__stepdetail">{children}</span>
		</div>
	);
}

type StageCall = NonNullable<SourceView["data"]>["stageCalls"][number];

/**
 * 구간 추출이 한 일 — 원본을 어떻게 나눴고, 경계를 어디로 당겼나.
 *
 * 조각 수는 메모리 상한이 정하고(ingest.plan_chunks) 경계는 **균등 지점 근처의 무음 한가운데**로
 * 당긴다. 고정 길이로 자르면 문장 한복판에서 끊겨 그 발화가 양쪽 조각에서 반토막 난다. 당긴 거리가
 * 곧 "문장을 안 자르려고 얼마나 움직였나" 다 — 근처에 무음이 없으면 0 이고 균등 지점에서 그대로 잘린다.
 * 균등 지점은 화면이 같은 규칙(범위 ÷ 조각 수)으로 되짚어 낸다 — 저장된 값이 아니다.
 */
function RangeOverview({
	chunks,
	duration,
	calls,
}: {
	chunks: SourceView["chunks"];
	duration: number;
	calls: StageCall[];
}) {
	const start = chunks[0].start_sec;
	const end = chunks[chunks.length - 1].end_sec;
	const step = (end - start) / chunks.length;
	const at = (sec: number) => `${Math.min(100, Math.max(0, (sec / duration) * 100))}%`;
	// stageCalls 는 최근 것이 앞이다.
	const latency = (stage: string) => calls.find((c) => c.stage === stage && c.error === null)?.latency_ms ?? null;
	const download = latency("download");
	const extract = latency("chunk");
	const seconds = (ms: number) => (ms < 10_000 ? `${(ms / 1000).toFixed(1)}초` : time(ms / 1000));

	return (
		<div className="sm-range-ov">
			<div className="sm-range-ov__bar" aria-hidden="true">
				{chunks.map((chunk, index) => (
					<span
						key={chunk.id}
						className="sm-range-ov__chunk"
						style={{ left: at(chunk.start_sec), width: at(chunk.end_sec - chunk.start_sec) }}
					>
						{index + 1}
					</span>
				))}
			</div>
			<ul className="sm-range-ov__cuts" aria-label="조각 경계">
				{chunks.slice(1).map((chunk, index) => {
					const shift = chunk.start_sec - (start + step * (index + 1));
					const moved = Math.abs(shift) >= 0.5;
					return (
						<li key={chunk.id}>
							<span className="sm-range-ov__cutat">{time(chunk.start_sec)}</span>
							{moved ? (
								<span>
									무음으로 {shift > 0 ? "+" : "−"}
									{Math.abs(shift).toFixed(0)}초 당김
								</span>
							) : (
								<span className="sm-meta">근처에 무음이 없어 균등 지점에서 자름</span>
							)}
						</li>
					);
				})}
			</ul>
			{(download !== null || extract !== null) && (
				<p className="sm-meta sm-range-ov__took">
					{download !== null && `원본 받기 ${seconds(download)}`}
					{download !== null && extract !== null && " · "}
					{extract !== null && `오디오 추출·분할 ${seconds(extract)}`}
				</p>
			)}
		</div>
	);
}

/** 서버 상한(stt.TERMS_MAX_CHARS)과 같은 값. whisper 는 힌트의 마지막 223 토큰만 쓴다 — 한글 글자당 약 0.95 토큰. */
const TERMS_LIMIT = 200;

/**
 * 음성 인식 설정 — 마지막으로 무엇으로 돌렸나(모델 · 언어 · 무음 거르기) + 용어 힌트.
 *
 * 🔴 용어 힌트(whisper 의 initial_prompt)는 학교 이름·전형 이름·고등학교 이름 같은 고유명사를 미리
 * 알려 줘서 잘못 알아듣는 일을 줄인다. 전사 품질을 가장 크게 가르는 값인데 예전에는 CLI 로만 넣을 수
 * 있었다(2026-10-24 화면으로 옮김). 목록은 원본에 저장되고 **다음 음성 인식부터** 붙는다.
 *
 * 칸에는 한 줄만 보이고, 넣고 빼는 일은 [용어 힌트] 버튼의 모달에서 한다(2026-10-24 사용자 피드백) —
 * 칩마다 × 를 달았더니 훑어보다 잘못 눌러 빠지는 자리가 되었고, 용어가 늘면 칸이 길어져 아래 조각
 * 목록을 밀어냈다.
 */
function SttSettings({
	call,
	languages,
	terms,
	fixes,
	transcribed,
	onSave,
	onSaveFixes,
}: {
	call: StageCall | null;
	languages: Record<string, string>;
	terms: string[];
	fixes: SttFix[];
	/** 전사가 한 번이라도 돌았나. 그래야 "바꾼 힌트는 다시 인식해야 붙는다" 가 말이 된다. */
	transcribed: boolean;
	onSave: (terms: string[]) => void;
	/** 저장이 끝나면 `fixed` 로 지금 전사에서 고친 발화 수를 알려 준다. */
	onSaveFixes: (fixes: SttFix[], fixed: (count: number) => void) => void;
}) {
	const [editing, setEditing] = useState(false);
	const [editingFixes, setEditingFixes] = useState(false);
	// 마지막 교정 저장이 지금 전사에서 몇 곳을 고쳤나. 저장했는데 아무 반응이 없으면 먹혔는지 알 수 없다.
	const [fixedNote, setFixedNote] = useState<number | null>(null);
	const params = call?.params ?? {};
	const language = typeof params.language === "string" ? params.language : null;
	const usedHint = typeof params.initial_prompt === "string" ? params.initial_prompt : "";
	const model = call?.model?.replace(/^faster-whisper:/, "whisper ") ?? "—";
	const changed = transcribed && terms.join(", ") !== usedHint;

	return (
		<div className="sm-stt">
			<dl className="sm-stt__facts">
				<div>
					<dt>모델</dt>
					<dd>{model}</dd>
				</div>
				<div>
					<dt>언어</dt>
					<dd>
						{call === null
							? "—"
							: language === null || language === "auto"
								? "자동 감지"
								: (languages[language] ?? language)}
					</dd>
				</div>
				<div>
					<dt>무음 거르기</dt>
					<dd>{call === null ? "—" : params.vad_filter === true ? "켬" : "끔"}</dd>
				</div>
				<div>
					<dt>마지막 인식</dt>
					<dd>{call === null ? "아직 없음" : new Date(call.created_at).toLocaleDateString("ko-KR")}</dd>
				</div>
			</dl>

			<div className="sm-stt__hint">
				<button type="button" className="sm-stt__open" onClick={() => setEditing(true)}>
					용어 힌트
					<span className="sm-stt__opencount">{terms.length}</span>
				</button>
				{terms.length > 0 ? (
					<ChipLine items={terms} onMore={() => setEditing(true)} />
				) : (
					<p className="sm-meta sm-stt__empty">
						없이 인식합니다. 학교·전형·고등학교 이름처럼 잘못 알아듣기 쉬운 고유명사를 넣어 두세요.
					</p>
				)}
			</div>
			{changed && (
				<p className="sm-stt__changed">
					지난 인식과 힌트가 다릅니다. 바꾼 힌트는 <b>다시 인식</b>해야 전사에 반영됩니다.
				</p>
			)}

			{/* 교정 사전 — 힌트로도 못 고친 말을 알아들은 뒤에 고친다(2026-10-24). 힌트에 "수능최저학력기준" 을
			    넣었는데도 small·large-v3·유튜브 자동자막이 모두 "수능체제학력" 으로 적었다. */}
			<div className="sm-stt__hint">
				<button type="button" className="sm-stt__open" onClick={() => setEditingFixes(true)}>
					교정 사전
					<span className="sm-stt__opencount">{fixes.length}</span>
				</button>
				{fixes.length > 0 ? (
					<ChipLine items={fixes.map((f) => `${f.from} → ${f.to}`)} onMore={() => setEditingFixes(true)} />
				) : (
					<p className="sm-meta sm-stt__empty">
						힌트를 넣어도 계속 틀리는 말을 고칩니다. 저장하면 지금 전사에 바로 적용됩니다.
					</p>
				)}
			</div>
			{fixedNote !== null && (
				<p className="sm-meta sm-stt__fixed">
					{fixedNote > 0 ? `지금 전사에서 발화 ${fixedNote.toLocaleString()}곳을 고쳤습니다.` : "지금 전사에는 고칠 곳이 없었습니다."}{" "}
					이미 만든 클립의 자막은 다시 만들어야 바뀝니다.
				</p>
			)}

			{editingFixes && (
				<FixesModal
					fixes={fixes}
					onClose={() => setEditingFixes(false)}
					onSave={(next) => {
						onSaveFixes(next, setFixedNote);
						setEditingFixes(false);
					}}
				/>
			)}

			{editing && (
				<TermsModal
					terms={terms}
					onClose={() => setEditing(false)}
					onSave={(next) => {
						onSave(next);
						setEditing(false);
					}}
				/>
			)}
		</div>
	);
}

/**
 * 용어 힌트 넣고 빼기. 모달 안에서 고친 목록은 [저장] 을 눌러야 한 번에 나간다 — 넣고 뺄 때마다
 * 저장하면 열 개를 정리하는 동안 요청이 열 번 나가고, 중간에 상한에 걸리면 절반만 저장된다.
 */
function TermsModal({
	terms,
	onClose,
	onSave,
}: {
	terms: string[];
	onClose: () => void;
	onSave: (terms: string[]) => void;
}) {
	const [list, setList] = useState(terms);
	const [draft, setDraft] = useState("");
	const length = list.join(", ").length;
	const over = length > TERMS_LIMIT;
	const dirty = list.join("\n") !== terms.join("\n");

	// 붙여넣은 "숭문고, 광성고" 는 둘로 넣는다. 이미 있는 것은 건너뛴다.
	function add() {
		const fresh: string[] = [];
		for (const raw of draft.split(/[,，、\n]/)) {
			const term = raw.replace(/\s+/g, " ").trim();
			if (term && !list.includes(term) && !fresh.includes(term)) {
				fresh.push(term);
			}
		}
		setList([...list, ...fresh]);
		setDraft("");
	}

	return (
		<Modal
			open
			title="용어 힌트"
			onClose={onClose}
			subhead="학교·전형·고등학교 이름처럼 잘못 알아듣기 쉬운 고유명사를 음성 인식에 미리 알려 줍니다. 다음 음성 인식부터 붙습니다."
		>
			<div className="sm-terms">
				<form
					className="sm-terms__add"
					onSubmit={(event) => {
						event.preventDefault();
						add();
					}}
				>
					<input
						className="sm-prep__input"
						type="text"
						value={draft}
						maxLength={200}
						placeholder="예) 숭문고, 광성고 — 쉼표로 여러 개를 한 번에"
						aria-label="추가할 용어"
						autoFocus
						onChange={(event) => setDraft(event.target.value)}
					/>
					<button type="submit" className="button sm-go" disabled={draft.trim() === ""}>
						추가
					</button>
				</form>

				<div className="sm-terms__head">
					<b>{list.length}개</b>
					<span className={`sm-stt__count${over ? " sm-stt__count--over" : ""}`}>
						{length} / {TERMS_LIMIT}자
					</span>
				</div>
				{list.length === 0 ? (
					<p className="sm-meta">아직 없습니다.</p>
				) : (
					<ul className="sm-terms__list">
						{list.map((term) => (
							<li key={term}>
								<span>{term}</span>
								<button
									type="button"
									className="button button--small"
									onClick={() => setList(list.filter((t) => t !== term))}
								>
									빼기
								</button>
							</li>
						))}
					</ul>
				)}
				{over && (
					<p className="sm-prep__warn">
						{TERMS_LIMIT}자를 넘었습니다. 넘치면 음성 인식이 앞쪽 용어를 말없이 버리므로 저장할 수 없습니다.
					</p>
				)}

				<div className="sm-terms__foot">
					<button type="button" className="button" onClick={onClose}>
						취소
					</button>
					<button
						type="button"
						className="button sm-go"
						disabled={!dirty || over}
						onClick={() => onSave(list)}
					>
						저장
					</button>
				</div>
			</div>
		</Modal>
	);
}

/**
 * 칩 한 줄 미리보기. 넘친 칩은 감추고 옆에 "외 N개" 로 센다 — 폭은 화면마다 달라 미리 알 수 없어서
 * 줄이 넘친 칩을 그려 놓고 센다. 용어 힌트와 교정 사전이 같이 쓴다.
 */
function ChipLine({ items, onMore }: { items: string[]; onMore: () => void }) {
	const line = useRef<HTMLUListElement>(null);
	const [hidden, setHidden] = useState(0);
	useLayoutEffect(() => {
		const list = line.current;
		if (!list) {
			setHidden(0);
			return;
		}
		const count = () => {
			const children = Array.from(list.children) as HTMLElement[];
			const top = children[0]?.offsetTop ?? 0;
			setHidden(children.filter((item) => item.offsetTop > top).length);
		};
		count();
		const observer = new ResizeObserver(count);
		observer.observe(list);
		return () => observer.disconnect();
	}, [items]);

	return (
		<div className="sm-stt__line">
			<ul className="sm-stt__terms" ref={line}>
				{items.map((item) => (
					<li key={item}>{item}</li>
				))}
			</ul>
			{hidden > 0 && (
				<button type="button" className="sm-linkbtn sm-stt__more" onClick={onMore}>
					외 {hidden}개
				</button>
			)}
		</div>
	);
}

/**
 * 교정 사전 넣고 빼기. 용어 힌트 모달처럼 [저장] 을 눌러야 한 번에 나간다 — 저장할 때마다 전사 전체에
 * 다시 적용되므로 한 줄씩 저장하면 그만큼 전사를 다시 훑는다.
 *
 * 🔴 짧은 말은 다른 말 속에도 들어 있다. "체제" → "최저" 는 진짜 "체제" 까지 바꾼다 — 그래서 앞뒤를
 * 붙여 길게 적으라고 안내한다("수능체제" → "수능최저"). 바른 말 안에 잘못 들린 말이 들어 있으면
 * 서버가 거절한다(고칠 때마다 늘어난다).
 */
function FixesModal({
	fixes,
	onClose,
	onSave,
}: {
	fixes: SttFix[];
	onClose: () => void;
	onSave: (fixes: SttFix[]) => void;
}) {
	const [list, setList] = useState(fixes);
	const [wrong, setWrong] = useState("");
	const [right, setRight] = useState("");
	const dirty = JSON.stringify(list) !== JSON.stringify(fixes);
	const from = wrong.replace(/\s+/g, " ").trim();
	const to = right.replace(/\s+/g, " ").trim();
	const duplicate = list.some((f) => f.from === from);
	const grows = from !== "" && to.includes(from);

	function add() {
		setList([...list, { from, to }]);
		setWrong("");
		setRight("");
	}

	return (
		<Modal
			open
			title="교정 사전"
			onClose={onClose}
			subhead="유튜브 자막이나 음성 인식이 계속 틀리게 적는 말을 바른 말로 바꿉니다. 저장하면 지금 대사에 바로 적용되고, 다음에 가져올 때도 붙습니다."
		>
			<div className="sm-terms">
				<form
					className="sm-terms__add"
					onSubmit={(event) => {
						event.preventDefault();
						add();
					}}
				>
					<input
						className="sm-prep__input"
						type="text"
						value={wrong}
						maxLength={40}
						placeholder="잘못 들린 말 — 예) 수능체제"
						aria-label="잘못 들린 말"
						autoFocus
						onChange={(event) => setWrong(event.target.value)}
					/>
					<input
						className="sm-prep__input"
						type="text"
						value={right}
						maxLength={40}
						placeholder="바른 말 — 예) 수능최저"
						aria-label="바른 말"
						onChange={(event) => setRight(event.target.value)}
					/>
					<button
						type="submit"
						className="button sm-go"
						disabled={from === "" || to === "" || from === to || duplicate || grows}
					>
						추가
					</button>
				</form>
				{duplicate && <p className="sm-prep__warn">"{from}" 은 이미 있습니다.</p>}
				{grows && (
					<p className="sm-prep__warn">
						바른 말 안에 잘못 들린 말이 들어 있으면 고칠 때마다 늘어납니다. 잘못 들린 말을 더 길게 적어 주세요.
					</p>
				)}
				<p className="sm-meta">
					짧은 말은 다른 말 속에도 들어 있습니다. "체제" 를 바꾸면 진짜 "체제" 도 바뀌니 앞뒤를 붙여 적으세요.
				</p>

				<div className="sm-terms__head">
					<b>{list.length}개</b>
				</div>
				{list.length === 0 ? (
					<p className="sm-meta">아직 없습니다.</p>
				) : (
					<ul className="sm-terms__list">
						{list.map((fix) => (
							<li key={fix.from}>
								<span>
									{fix.from} → <b>{fix.to}</b>
								</span>
								<button
									type="button"
									className="button button--small"
									onClick={() => setList(list.filter((f) => f.from !== fix.from))}
								>
									빼기
								</button>
							</li>
						))}
					</ul>
				)}
				<p className="sm-meta">빼도 이미 고친 글자는 되돌아가지 않습니다.</p>

				<div className="sm-terms__foot">
					<button type="button" className="button" onClick={onClose}>
						취소
					</button>
					<button type="button" className="button sm-go" disabled={!dirty} onClick={() => onSave(list)}>
						저장
					</button>
				</div>
			</div>
		</Modal>
	);
}

/**
 * 구간 타임라인(C3) — 원본 전체 길이 위에 구간을 길이대로 늘어놓는다. 하나를 누르면 아래에
 * 설명과 전사가 펼쳐진다.
 *
 * 구간 66개를 막대로만 두면 2초짜리는 2px 라 누를 수가 없다. 그래서 막대 아래에 **목록**도 둔다 —
 * 막대는 "어디쯤 · 얼마나 긴가" 를 보는 자리, 목록은 고르는 자리다. 둘이 같은 선택을 나눠 쓴다.
 */
function SegmentTimeline({
	segments,
	chunks,
	duration,
	clipSegments,
}: {
	segments: SourceView["segments"];
	chunks: SourceView["chunks"];
	duration: number;
	/** 클립이 만들어진 구간 번호. 막대와 목록에 표시한다. */
	clipSegments: Set<number>;
}) {
	const [selected, setSelected] = useState<number>(() => segments[0]?.idx ?? 0);
	// 조각별 전사. 구간을 고를 때 그 조각만 한 번 읽고 들고 있는다 — 3천 줄을 처음부터 다 받지 않는다.
	const [lines, setLines] = useState<Record<number, ShortsUtterance[]>>({});
	const [loadError, setLoadError] = useState<string | null>(null);

	const segment = segments.find((s) => s.idx === selected) ?? segments[0];
	const chunkId = segment?.chunk_id;

	useEffect(() => {
		if (chunkId === undefined || lines[chunkId]) {
			return;
		}
		let alive = true;
		fetchUtterances(chunkId)
			.then((rows) => alive && setLines((prev) => ({ ...prev, [chunkId]: rows })))
			.catch((e: unknown) => alive && setLoadError(e instanceof Error ? e.message : String(e)));
		return () => {
			alive = false;
		};
	}, [chunkId, lines]);

	if (!segment) {
		return null;
	}
	const at = (sec: number) => `${Math.min(100, Math.max(0, (sec / duration) * 100))}%`;
	const transcript = (lines[segment.chunk_id] ?? []).filter(
		(u) => u.idx >= segment.start_utterance_idx && u.idx <= segment.end_utterance_idx,
	);

	return (
		<div className="sm-segs">
			<div className="sm-segs__bar" role="group" aria-label="구간 타임라인">
				{segments.map((s) => (
					<button
						key={s.id}
						type="button"
						tabIndex={-1}
						className={`sm-segs__seg${s.idx === segment.idx ? " sm-segs__seg--on" : ""}${clipSegments.has(s.idx) ? " sm-segs__seg--clip" : ""}${s.excluded_by ? " sm-segs__seg--out" : ""}`}
						style={{ left: at(s.start_sec), width: at(s.end_sec - s.start_sec) }}
						title={`#${s.idx + 1} ${time(s.start_sec)} · ${s.description ?? ""}`}
						onClick={() => setSelected(s.idx)}
					/>
				))}
				{/* 처리 조각 경계. 전사가 조각마다 따로 돌아서, 경계에서 문장이 어긋나면 여기서 보인다. */}
				{chunks.slice(1).map((c) => (
					<span key={c.id} className="sm-segs__cut" style={{ left: at(c.start_sec) }} aria-hidden="true" />
				))}
			</div>
			<div className="sm-segs__ticks" aria-hidden="true">
				<span>0:00</span>
				<span>{time(duration / 2)}</span>
				<span>{time(duration)}</span>
			</div>
			<p className="sm-segs__legend">
				<span className="sm-segs__key sm-segs__key--clip" /> 클립이 나온 구간
				<span className="sm-segs__key sm-segs__key--cut" /> 처리 조각 경계
			</p>

			<div className="sm-segs__split">
				<ol className="sm-segs__list" aria-label="구간 목록">
					{segments.map((s) => (
						<li key={s.id}>
							<button
								type="button"
								className={`sm-segs__row${s.idx === segment.idx ? " sm-segs__row--on" : ""}`}
								aria-current={s.idx === segment.idx ? "true" : undefined}
								onClick={() => setSelected(s.idx)}
							>
								<span className="sm-segs__time">{time(s.start_sec)}</span>
								<span className="sm-segs__desc">{s.description ?? "설명 없음"}</span>
								{clipSegments.has(s.idx) && <span className="sm-segs__dot" aria-label="클립 있음" />}
							</button>
						</li>
					))}
				</ol>

				<article className="sm-segs__detail" aria-live="polite">
					<header className="sm-segs__dhead">
						<b>
							#{segment.idx + 1} · {time(segment.start_sec)}–{time(segment.end_sec)}
						</b>
						<span className="sm-meta">{Math.round(segment.end_sec - segment.start_sec)}초</span>
						{clipSegments.has(segment.idx) && <span className="sm-bar__chip sm-bar__chip--ok">클립 있음</span>}
						{segment.excluded_by && (
							<span className="tag tag--rejected" title={segment.excluded_reason ?? undefined}>
								제외됨
							</span>
						)}
					</header>
					{segment.description && <p className="sm-segs__ddesc">{segment.description}</p>}
					<div className="sm-segs__text">
						{loadError ? (
							<p className="state state--error">전사를 읽지 못했습니다 ({loadError})</p>
						) : transcript.length === 0 ? (
							<p className="sm-meta">전사를 불러오는 중…</p>
						) : (
							transcript.map((u) => (
								<p key={u.idx}>
									<span className="sm-segs__ut">{time(u.start_sec)}</span>
									{u.text}
								</p>
							))
						)}
					</div>
				</article>
			</div>
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


/**
 * 원본 정보 — 제목 · 유튜브 id · 영상 개요. 사람이 고치는 값 셋을 한 자리에 모은다.
 *
 * **제목**은 파일로 등록한 원본이면 파일 이름이다(`media/{name}/register` 가 `path.stem` 을 쓴다).
 * 🔴 그 값이 시청자 화면까지 그대로 나가서 `MVqTWMg4n0o` 가 사람 앞에 보였다(2026-09-19).
 *
 * **유튜브 id** 가 없으면 시청자가 원본을 못 본다(임베드 플레이어가 이 값으로 연다). 파일로
 * 올린 원본에는 없어서, 전에는 psql 로 넣어야 했다. URL 을 통째로 붙여넣어도 서버가 id 만 뽑는다.
 *
 * **영상 개요**(프롬프트 3층)는 구간 분할·순위·자르기·후보 생성 **모든 호출에 붙는다.** 그래서
 * 짧아야 하고(500자) 사실만 적는다 — "무엇을 좋게 볼지" 는 프롬프트 화면의 관리자 기준에 적는다.
 * 🔴 **이미 나눈 구간에는 반영되지 않는다.** 구간은 캐시된 자산이라 다시 나누기 전까지 그대로다.
 */
function SourceInfoEditor({
	sourceId,
	savedTitle,
	savedYoutubeId,
	savedContext,
	disabled,
	onSaved,
	canDraft,
	onDraft,
	draftedContext,
}: {
	sourceId: number;
	savedTitle: string;
	savedYoutubeId: string | null;
	savedContext: string | null;
	disabled: boolean;
	onSaved: () => void;
	/** 구간 요약이 있는가. 없으면 개요를 쓸 재료가 없다. */
	canDraft: boolean;
	onDraft: () => void;
	/** 방금 끝난 개요 초안. 저장하지 않고 입력칸에만 들어간다. */
	draftedContext: string | null;
}) {
	const [titleDraft, setTitleDraft] = useState<string | null>(null);
	const [youtubeDraft, setYoutubeDraft] = useState<string | null>(null);
	const [contextDraft, setContextDraft] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// 초안이 도착하면 입력칸에 **붓기만** 한다. 저장은 사람이 누른다 — 이 글은 이후 모든 호출에
	// 붙어서 틀린 한 줄이 조용히 파이프라인 전체에 퍼진다.
	//
	// 🔴 사람이 이미 손댄 개요는 덮지 않는다. 초안을 기다리는 동안 직접 쓰고 있었을 수 있고,
	// 그걸 지우면 그 사람의 문장은 어디에도 남지 않는다.
	useEffect(() => {
		if (draftedContext === null) {
			return;
		}
		setContextDraft((current) => (current === null ? draftedContext : current));
	}, [draftedContext]);

	const title = titleDraft ?? savedTitle;
	const youtubeId = youtubeDraft ?? savedYoutubeId ?? "";
	const context = contextDraft ?? savedContext ?? "";

	const titleDirty = title.trim() !== savedTitle.trim();
	const youtubeDirty = youtubeId.trim() !== (savedYoutubeId ?? "").trim();
	const contextDirty = context.trim() !== (savedContext ?? "").trim();
	const dirty = titleDirty || youtubeDirty || contextDirty;
	const over = context.trim().length > 500;
	const emptyTitle = title.trim().length === 0;

	const save = async () => {
		setBusy(true);
		setError(null);
		try {
			// 🔴 고친 것만 보낸다. 안 고친 값을 실어 보내면 다른 창의 수정을 옛 값으로 덮어쓴다.
			await patchSource(sourceId, {
				...(titleDirty ? { title } : {}),
				...(youtubeDirty ? { youtubeId } : {}),
				...(contextDirty ? { context } : {}),
			});
			setTitleDraft(null);
			setYoutubeDraft(null);
			setContextDraft(null);
			onSaved();
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(false);
		}
	};

	return (
		<>
			<label className="sm-prep__row">
				<span className="sm-prep__label">
					제목
					<span className="sm-meta">시청자 화면에 그대로 보입니다</span>
				</span>
				<span className="sm-prep__value">
					<input
						className="sm-prep__input"
						type="text"
						value={title}
						maxLength={300}
						placeholder="영상 제목"
						onChange={(event) => setTitleDraft(event.target.value)}
					/>
					{emptyTitle && <span className="sm-prep__warn">제목은 비울 수 없습니다</span>}
				</span>
			</label>
			<label className="sm-prep__row">
				<span className="sm-prep__label">
					유튜브 id
					<span className="sm-meta">시청자 화면의 플레이어가 이 값으로 원본을 엽니다. 주소를 붙여넣어도 됩니다</span>
				</span>
				<span className="sm-prep__value">
					<input
						className="sm-prep__input"
						type="text"
						value={youtubeId}
						maxLength={200}
						placeholder="예) MVqTWMg4n0o 또는 https://youtu.be/MVqTWMg4n0o"
						onChange={(event) => setYoutubeDraft(event.target.value)}
					/>
				</span>
			</label>
			<div className="sm-prep__row">
				<label className="sm-prep__label" htmlFor={`overview-${sourceId}`}>
					영상 개요
					<span className="sm-meta">
						이 영상이 무엇인지 한두 문장. 이후 순위·자르기·답하기에 붙고, 이미 나눈 구간에는 반영되지 않습니다
					</span>
				</label>
				<span className="sm-prep__value">
					<textarea
						id={`overview-${sourceId}`}
						className="sm-prep__input"
						rows={3}
						value={context}
						maxLength={600}
						placeholder="예) 2027학년도 입학전형 설명회. 수시·정시 전형별 기준과 학사제도를 안내한다."
						onChange={(event) => setContextDraft(event.target.value)}
					/>
					<span className="sm-actions">
						<span className={`sm-meta${over ? " pr-counter--over" : ""}`}>
							{context.trim().length} / 500자
						</span>
						{/* 🔴 눌리지 않는 버튼을 두지 않는다 — 이유를 말할 기회가 없어 "고장난 화면" 으로 읽힌다.
						    재료가 없을 때는 버튼 대신 무엇이 필요한지를 적는다. */}
						{canDraft ? (
							<button
								type="button"
								className="button button--small"
								onClick={onDraft}
								disabled={disabled || busy}
							>
								AI로 초안 쓰기
							</button>
						) : (
							<span className="sm-meta">주제 분할을 끝내면 AI가 초안을 써 줍니다</span>
						)}
					</span>
				</span>
			</div>
			<div className="sm-prep__save">
				{error && <p className="state state--error">{error}</p>}
				<button
					type="button"
					className={`button button--small${dirty ? " sm-go" : ""}`}
					onClick={save}
					disabled={disabled || busy || over || emptyTitle || !dirty}
				>
					{busy ? "저장 중…" : dirty ? "원본 정보 저장" : "고친 내용 없음"}
				</button>
			</div>
		</>
	);
}
