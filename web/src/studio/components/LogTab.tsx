import { fetchUtterances } from "../api";
import { time } from "../format";
import { STAGE_LABEL } from "../useStudioJob";
import type { ShortsCost } from "../types";
import type { SourceView } from "../SourcePage";

/**
 * 기록 탭 — 전사·비용·단계 기록. 뭔가 이상해 보일 때만 여는 자리라 세 덩어리 모두 접어 둔다.
 */
export function LogTab({ view }: { view: SourceView }) {
	const { data, act, chunks, hasChunks, utterances, transcript, setTranscript } = view;

	return (
		<>
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
		</>
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
