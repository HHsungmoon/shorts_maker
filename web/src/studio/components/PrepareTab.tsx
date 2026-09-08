import { createChunk, publishSource, runSegment, runStt } from "../api";
import { Step } from "./Step";
import { time } from "../format";
import type { SourceView } from "../SourcePage";

/**
 * 영상 준비 탭 — 1~4단계. 영상당 한 번 하고 다시 볼 일이 없어서 매일 쓰는 질문 탭과
 * 자리를 나눴다.
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
		coveredSec,
		duration,
		chunkStart,
		chunkEnd,
		segments,
		nextStep,
		editingRange,
		rangeStart,
		rangeEnd,
		rangeValid,
		setRange,
		setRangeOpen,
		language,
		setLanguage,
	} = view;

	return (
		<>
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
		</>
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
