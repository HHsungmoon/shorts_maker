import { useCallback, useEffect, useRef, useState } from "react";
import {
	clipUrl,
	createChunk,
	deleteMedia,
	fetchJob,
	fetchMedia,
	fetchShortsSource,
	fetchStatus,
	fetchUtterances,
	renderClip,
	reviewClip,
	runCut,
	runRank,
	runSegment,
	runStt,
	segmentPreviewUrl,
} from "../api/shorts";
import { useAuth } from "../auth/AuthContext";
import { ClipVideo } from "../components/ClipVideo";
import { MediaLibrary } from "../components/MediaLibrary";
import { NewSourceModal } from "../components/NewSourceModal";
import { Step } from "../components/Step";
import { useAsync } from "../hooks/useAsync";
import type {
	ShortsClip,
	ShortsCost,
	ShortsJob,
	ShortsMediaList,
	ShortsSegment,
	ShortsSourceDetail,
	ShortsStatus,
	ShortsUtterance,
} from "../types";

const DEFAULT_CRITERIA = "한 문장으로 인용할 만한 핵심 논지";

const STAGE_LABEL: Record<string, string> = {
	download: "영상 받기",
	register: "원본 등록",
	chunk: "구간 추출",
	stt: "음성 인식",
	segment: "주제 분할",
	rank: "선정",
	cut: "클립 만들기",
	render: "렌더",
	pipeline: "전체 실행",
};

function time(seconds: number): string {
	const total = Math.round(seconds);
	return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function elapsed(since: string): string {
	return `${Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 1000))}초`;
}

export function ShortsPage() {
	const { authRequired, signOut } = useAuth();
	const [picked, setPicked] = useState<number | null>(null);
	const [reloadToken, setReloadToken] = useState(0);
	const [job, setJob] = useState<ShortsJob | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [notice, setNotice] = useState<string | null>(null);
	const [criteria, setCriteria] = useState(DEFAULT_CRITERIA);
	const [fromMin, setFromMin] = useState(0);
	const [toMin, setToMin] = useState(30);
	const [openPreview, setOpenPreview] = useState<number | null>(null);
	const [transcript, setTranscript] = useState<ShortsUtterance[] | null>(null);
	const [language, setLanguage] = useState<string | null>(null);
	const [modalOpen, setModalOpen] = useState(false);
	const pollRef = useRef<number | null>(null);

	const status = useAsync<ShortsStatus>(fetchStatus, [reloadToken]);
	const media = useAsync<ShortsMediaList>(fetchMedia, [reloadToken]);
	const registered = (media.data?.items ?? []).filter((i) => i.sourceId !== null);
	const sourceId = picked ?? registered[0]?.sourceId ?? null;
	const detail = useAsync<ShortsSourceDetail | null>(
		() => (sourceId === null ? Promise.resolve(null) : fetchShortsSource(sourceId)),
		[sourceId, reloadToken],
	);

	const busy = job !== null && job.status !== "DONE" && job.status !== "FAILED";

	useEffect(() => {
		if (!job || !busy) {
			return;
		}
		pollRef.current = window.setTimeout(async () => {
			try {
				const next = await fetchJob(job.id);
				setJob(next);
				if (next.status === "DONE" || next.status === "FAILED") {
					setReloadToken((n) => n + 1);
					setError(next.error);
				}
			} catch (e: unknown) {
				setError(e instanceof Error ? e.message : String(e));
			}
		}, 2000);
		return () => {
			if (pollRef.current) {
				window.clearTimeout(pollRef.current);
			}
		};
	}, [job, busy]);

	const submit = useCallback(async (start: () => Promise<ShortsJob>) => {
		setError(null);
		setNotice(null);
		try {
			setJob(await start());
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		}
	}, []);

	const act = useCallback(async (run: () => Promise<unknown>) => {
		setError(null);
		try {
			await run();
			setReloadToken((n) => n + 1);
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		}
	}, []);

	// 🔴 서버에서 파일과 파생물을 실제로 지운다. 되돌릴 수 없어 무엇이 사라지는지 먼저 말한다.
	const remove = useCallback(
		async (name: string) => {
			const item = media.data?.items.find((i) => i.name === name);
			const extra = item?.sourceId ? "\n전사·구간·클립도 함께 지워집니다." : "";
			if (!window.confirm(`${name} 을(를) 서버에서 삭제합니다.${extra}\n되돌릴 수 없습니다.`)) {
				return;
			}
			setError(null);
			try {
				const result = await deleteMedia(name);
				const freed = (result.freedBytes / (1 << 20)).toFixed(0);
				setNotice(`${result.removed.length}개 파일 삭제 · ${freed}MB 확보`);
				if (item?.sourceId === sourceId) {
					setPicked(null);
				}
				setReloadToken((n) => n + 1);
			} catch (e: unknown) {
				setError(e instanceof Error ? e.message : String(e));
			}
		},
		[media.data, sourceId],
	);

	const data = detail.data;
	const geminiReady = status.data?.geminiKey === true;
	const chunk = data?.chunks[0] ?? null;
	const utterances = data ? data.chunks.reduce((n, c) => n + c.utteranceCount, 0) : 0;
	const segments: ShortsSegment[] = data ? data.chunks.flatMap((c) => c.segments) : [];
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

	// 다음에 눌러야 할 단계 하나만 강조한다.
	const nextStep = !data ? 1 : !chunk ? 2 : utterances === 0 ? 3 : segments.length === 0 ? 4 : 5;

	return (
		<div className="page">
			<div className="page-head">
				<h1 className="page-title">shorts_maker</h1>
				{data && (
					<span className="page-count">
						발화 {utterances} · 구간 {segments.length} · 클립 {data.clips.length}
					</span>
				)}
				<span className="sm-actions sm-actions--end">
					<button
						type="button"
						className="button button--small sm-go"
						onClick={() => setModalOpen(true)}
					>
						+ 새로 만들기
					</button>
					{authRequired && (
						<button type="button" className="button button--small" onClick={signOut}>
							로그아웃
						</button>
					)}
				</span>
			</div>

			{status.error && (
				<div className="notice">
					서버 상태를 읽지 못했습니다. shorts_maker 가 떠 있는지 확인하세요 ({status.error.message}).
				</div>
			)}
			{status.data && !geminiReady && (
				<div className="notice">GEMINI_API_KEY 가 없어 4·5단계를 실행할 수 없습니다.</div>
			)}
			{error && <p className="state state--error">{error}</p>}
			{notice && <div className="notice">{notice}</div>}
			{busy && job && (
				<div className="notice">
					{STAGE_LABEL[job.kind] ?? job.kind} 진행 중… ({elapsed(job.createdAt)} 경과) · 끝날
					때까지 다른 실행은 대기합니다
				</div>
			)}

			<NewSourceModal
				open={modalOpen}
				onClose={() => setModalOpen(false)}
				unregistered={(media.data?.items ?? []).filter((i) => i.sourceId === null)}
				languages={status.data?.languages ?? { ko: "한국어", en: "영어" }}
				onStarted={setJob}
			/>

			{media.data && (
				<MediaLibrary
					media={media.data}
					selectedSourceId={sourceId}
					onSelect={(id) => {
						setPicked(id);
						setTranscript(null);
						setOpenPreview(null);
					}}
					onDelete={remove}
					busy={busy}
				/>
			)}

			<h2 className="section-title">진행 단계</h2>

			<Step
				no={1}
				title="원본"
				done={data !== null}
				next={nextStep === 1}
				detail={
					data
						? `${data.source.title} · ${time(data.source.duration_sec ?? 0)}`
						: "위에서 선택하거나 새로 만드세요"
				}
			/>

			<Step
				no={2}
				title="구간 추출"
				done={chunk !== null}
				next={nextStep === 2}
				detail={
					chunk
						? `${time(chunk.start_sec)} ~ ${time(chunk.end_sec)}`
						: "분석할 구간의 오디오를 뽑습니다"
				}
				actions={
					data && (
						<>
							<input
								className="field__input"
								style={{ width: 56 }}
								type="number"
								min={0}
								value={fromMin}
								onChange={(e) => setFromMin(Number(e.target.value))}
								aria-label="시작 분"
							/>
							<span className="sm-meta">~</span>
							<input
								className="field__input"
								style={{ width: 56 }}
								type="number"
								min={1}
								value={toMin}
								onChange={(e) => setToMin(Number(e.target.value))}
								aria-label="끝 분"
							/>
							<span className="sm-meta">분</span>
							<button
								type="button"
								className={`button button--small${nextStep === 2 ? " sm-go" : ""}`}
								disabled={busy || toMin <= fromMin}
								onClick={() => submit(() => createChunk(data.source.id, fromMin * 60, toMin * 60))}
							>
								{chunk ? "다시 추출" : "추출"}
							</button>
						</>
					)
				}
			/>

			<Step
				no={3}
				title="음성 인식"
				done={utterances > 0}
				next={nextStep === 3}
				detail={utterances > 0 ? `발화 ${utterances}개` : "몇 분 걸립니다"}
				actions={
					chunk && (
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
							<button
								type="button"
								className={`button button--small${nextStep === 3 ? " sm-go" : ""}`}
								disabled={busy}
								onClick={() =>
									submit(() => runStt(chunk.id, language ?? data?.source.language ?? null))
								}
							>
								{utterances > 0 ? "다시" : "실행"}
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
					chunk && (
						<button
							type="button"
							className={`button button--small${nextStep === 4 ? " sm-go" : ""}`}
							disabled={busy || utterances === 0 || !geminiReady}
							onClick={() => submit(() => runSegment(chunk.id))}
						>
							{segments.length > 0 ? "다시" : "실행"}
						</button>
					)
				}
			/>

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
								disabled={busy || segments.length === 0 || !geminiReady}
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
					<h2 className="section-title">
						6·7단계 — 구간을 골라 클립을 만들고 렌더합니다
						{latestRun?.criteria_prompt && (
							<span className="page-count"> · 기준: {latestRun.criteria_prompt}</span>
						)}
					</h2>
					{latestRun?.error && <p className="state state--error">{latestRun.error}</p>}

					{ranked.map((entry, rank) => {
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
												disabled={busy || !geminiReady}
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

			{data && data.clips.length > 0 && (
				<>
					<h2 className="section-title">완성 클립</h2>
					{data.clips.map((clip) => (
						<section className="sm-panel sm-clip" key={clip.id}>
							{clip.rendered ? (
								<ClipVideo src={clipUrl(clip.id)} width={220} />
							) : (
								<button
									type="button"
									className="button button--small sm-go"
									disabled={busy}
									onClick={() => submit(() => renderClip(clip.id))}
								>
									7. 렌더하기
								</button>
							)}
							<div style={{ flex: 1, minWidth: 0 }}>
								<div>
									<strong>클립 {clip.id}</strong>{" "}
									<span className="sm-meta">
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
								</div>
							</div>
						</section>
					))}
				</>
			)}

			{data && (
				<>
					<h2 className="section-title">참고</h2>

					<details className="sm-fold">
						<summary>전사 {utterances}발화</summary>
						{chunk && (
							<div className="sm-actions" style={{ margin: "12px 0" }}>
								<button
									type="button"
									className="button button--small"
									onClick={() =>
										transcript
											? setTranscript(null)
											: act(async () => setTranscript(await fetchUtterances(chunk.id)))
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
											<tr key={u.idx}>
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
