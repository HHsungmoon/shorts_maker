import { fetchInsights, fetchStageCallBody, fetchUtterances } from "../api";
import { time } from "../../shared/format";
import { useState } from "react";
import { useAsync } from "../../shared/useAsync";
import { STAGE_LABEL } from "../useStudioJob";
import type {
	ShortsCost,
	ShortsFunnelRow,
	ShortsInsights,
	ShortsStageCall,
	ShortsStageCallBody,
} from "../types";
import type { SourceView } from "../SourcePage";

/**
 * 기록 탭 — **성과 기록**(퍼널)과 작업 기록(전사·비용·단계).
 *
 * 퍼널은 접지 않는다. 나머지는 뭔가 이상해 보일 때만 여는 자리라 접어 두지만, "숏폼이 원본
 * 유입을 만들었나" 는 디버깅 질문이 아니라 이 제품이 옳은지를 묻는 질문이다(tease §9) —
 * 접어 두면 아무도 안 본다.
 */
export function LogTab({ view }: { view: SourceView }) {
	const { sourceId, data, act, chunks, hasChunks, utterances, transcript, setTranscript } = view;

	return (
		<>
			<FunnelSection sourceId={sourceId} />

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
										<th>본문</th>
									</tr>
								</thead>
								<tbody>
									{data.stageCalls.map((call) => (
										<StageCallRow key={call.id} call={call} />
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

/**
 * 단계 기록 한 줄. **프롬프트와 원본 응답을 펼쳐 볼 수 있다.**
 *
 * 🔴 이게 있는 이유: 판정이 이상해 보일 때 "모델이 무엇을 보고 무엇을 답했는지" 를 확인할 방법이
 * 없었다. 같은 호출을 다시 하는 것뿐이었고, 무료 등급에서는 그 재현이 하루 할당량을 깎는다.
 *
 * 본문은 **누를 때 읽는다.** 목록에 미리 실으면 화면 한 번에 수 MB 가 오간다.
 */
function StageCallRow({ call }: { call: ShortsStageCall }) {
	const [body, setBody] = useState<ShortsStageCallBody | null>(null);
	const [busy, setBusy] = useState(false);
	const [failed, setFailed] = useState(false);

	const toggle = async () => {
		if (body) {
			setBody(null);
			return;
		}
		setBusy(true);
		setFailed(false);
		try {
			setBody(await fetchStageCallBody(call.id));
		} catch {
			setFailed(true);
		} finally {
			setBusy(false);
		}
	};

	return (
		<>
			<tr>
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
				<td>
					{/* 🔴 본문이 없는 기록도 많다 — LLM 이 아닌 단계(전사·렌더·청크)와, 이 컬럼이
					    생기기 전(2026-09-09)의 호출이다. 없는 것에 버튼을 두면 눌러도 빈 칸이 나온다. */}
					{call.has_body ? (
						<button type="button" className="button button--small" onClick={toggle} disabled={busy}>
							{busy ? "…" : body ? "닫기" : "보기"}
						</button>
					) : (
						<span className="sm-meta">-</span>
					)}
				</td>
			</tr>
			{failed && (
				<tr>
					<td colSpan={5} className="state state--error">
						본문을 읽지 못했습니다.
					</td>
				</tr>
			)}
			{body && (
				<tr>
					<td colSpan={5}>
						{/* 프롬프트가 먼저다 — "무엇을 보았나" 를 알고 나서야 응답이 읽힌다. */}
						<Pane title="프롬프트" text={body.prompt} />
						<Pane title="원본 응답" text={body.response} />
						{body.error && <Pane title="오류" text={body.error} />}
					</td>
				</tr>
			)}
		</>
	);
}

function Pane({ title, text }: { title: string; text: string | null }) {
	if (!text) {
		return null;
	}
	return (
		<details className="sm-fold" open>
			<summary>
				{title} <span className="sm-meta">{text.length.toLocaleString()}자</span>
			</summary>
			{/* 🔴 프롬프트는 줄바꿈과 들여쓰기가 뜻을 갖는다(구간 머리글·번호 붙은 대사 줄).
			    한 줄로 흘리면 어느 구간의 몇 번째 줄인지 읽을 수 없다. */}
			<pre className="sm-pre">{text}</pre>
		</details>
	);
}

// 퍼널 칸의 한글 이름. 순서는 서버(`events.FUNNEL_KINDS`)가 정하고 여기서는 이름만 붙인다 —
// 순서를 두 곳에서 정하면 어긋난다.
const FUNNEL_LABEL: Record<string, string> = {
	short_play: "재생",
	short_complete: "완주",
	cta_click: "원본 보기 누름",
	origin_seek: "원본으로 이동",
	origin_play: "원본 재생",
};

// 서버가 정한 칸 순서대로 값을 꺼낸다. 칸 이름이 그대로 필드 이름이라 인덱스 접근이 필요하다 —
// 화면에 순서를 다시 적지 않으려고 지불하는 비용이고, 모르는 칸은 0 으로 둔다.
function funnelCount(row: ShortsFunnelRow, kind: string): number {
	const value = (row as unknown as Record<string, unknown>)[kind];
	return typeof value === "number" ? value : 0;
}

/**
 * 숏폼별 퍼널 (tease §9, update_plan M6b).
 *
 * 🔴 **비율을 쓰지 않는다.** 3명 중 1명을 33% 로 적으면 거짓말이 된다. 분모가 수백이 되면
 * 그때 다시 판단한다 — 지금은 사람 수를 그대로 보여주는 게 유일하게 정직한 표시다.
 */
function FunnelSection({ sourceId }: { sourceId: number }) {
	const insights = useAsync<ShortsInsights>(() => fetchInsights(sourceId), [sourceId]);

	if (insights.loading) {
		return <p className="state">불러오는 중…</p>;
	}
	if (insights.error || !insights.data) {
		return <p className="state state--error">퍼널을 읽지 못했습니다.</p>;
	}
	const { kinds, clips, totals } = insights.data;

	return (
		<>
			<h2 className="section-title">시청자 반응</h2>
			{clips.length === 0 ? (
				<p className="state">
					발행된 숏폼이 없습니다. 숏폼을 발행하면 시청자가 원본으로 넘어가는 흐름이 여기 쌓입니다.
				</p>
			) : (
				<>
					<div className="table-wrap">
						<table className="table">
							<thead>
								<tr>
									<th>숏폼</th>
									{kinds.map((kind) => (
										<th key={kind}>{FUNNEL_LABEL[kind] ?? kind}</th>
									))}
								</tr>
							</thead>
							<tbody>
								{clips.map((row) => (
									<tr key={row.clip_id}>
										<td>
											{row.label ?? <span className="sm-meta">제목 없음</span>}
											{/* 질문에서 나온 것과 크리에이터가 직접 뽑은 것을 구분해 준다 —
											    같은 표에 섞여 있어서 표시가 없으면 알 수 없다. */}
											{row.question === null && <span className="tag">직접 선정</span>}
										</td>
										{kinds.map((kind) => (
											<td key={kind} className="sm-meta">
												{funnelCount(row, kind)}
											</td>
										))}
									</tr>
								))}
							</tbody>
						</table>
					</div>
					<p className="sm-meta">
						단위는 <strong>사람 수</strong>입니다 — 같은 사람이 여러 번 봐도 1로 셉니다. “원본 재생”은
						원본 보기를 누른 뒤 30초 안에 재생이 시작된 경우만 셉니다(그 전부터 보고 있던 재생을
						유입으로 세면 숫자가 거짓이 됩니다).
					</p>
				</>
			)}
			<p className="sm-meta">
				이 영상에 다녀간 사람 {totals.viewers ?? 0}명 · 질문 남김 {totals.question_post ?? 0}명 ·
				좋아요 누름 {totals.like ?? 0}명
			</p>
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
