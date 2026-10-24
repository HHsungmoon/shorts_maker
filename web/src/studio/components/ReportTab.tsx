import { useState } from "react";
import { Link } from "react-router-dom";
import { fetchReport } from "../api";
import { useAsync } from "../../shared/useAsync";
import type { ShortsReport, ShortsReportQuestion } from "../types";
import type { SourceView } from "../SourcePage";

/**
 * 회차 리포트 — **이 설명회가 답하지 않은 것**.
 *
 * 🔴 다른 탭과 목적이 다르다. 질문 탭은 크리에이터가 **작업하는** 자리고, 여기는 회차가 끝난 뒤
 * **가져가는** 자리다(기획서 §05-③). 다음 설명회 큐시트이자 공고에서 빠진 정보 목록이다.
 * 그래서 화면에서 읽는 것만큼 **복사해 가져갈 수 있는 것**이 중요하다 — 담당자가 이걸 윗선에
 * 보내야 예산이 난다.
 *
 * 화면은 맨 위 숫자 여섯 → 많이 물은 질문 막대표 → 두 칸(왼쪽 못 답한 것·할 일, 오른쪽 답한 것·비용)
 * 순이다(개선안 F1 + F2 의 막대표, 2026-10-24). 예전엔 숫자가 한 문장 안에 묻혀 있었다.
 *
 * 🔴 기회손실 추정치("예상 이탈 지원자 약 18명")는 넣지 않는다. 전환 표본이 0이라 근거가 없고,
 * 근거 없는 숫자 하나가 리포트의 나머지 숫자까지 의심받게 만든다.
 */
export function ReportTab({ view }: { view: SourceView }) {
	const { sourceId } = view;
	const report = useAsync<ShortsReport>(
		() => fetchReport(sourceId), [sourceId], { keepPrevious: true, subject: sourceId },
	);
	const [copied, setCopied] = useState(false);

	if (report.loading) {
		return <p className="state">불러오는 중…</p>;
	}
	if (report.error || !report.data) {
		return <p className="state state--error">리포트를 읽지 못했습니다.</p>;
	}
	const data = report.data;
	const { summary, cost } = data;

	const copy = async () => {
		try {
			await navigator.clipboard.writeText(asText(data));
			setCopied(true);
			setTimeout(() => setCopied(false), 2000);
		} catch {
			setCopied(false);
		}
	};

	// 막대표에 올릴 질문 — 상태와 상관없이 전부, 물은 사람 순. 같은 질문이 두 목록에 들지 않는다(서버가 가른다).
	const ranked: ChartRow[] = [
		...data.answered.map((row) => ({ id: row.clusterId, question: row.question, askedBy: row.askedBy, likes: row.likes, status: "answered" as const })),
		...data.missing.map((row) => ({ id: row.clusterId, question: row.question, askedBy: row.askedBy, likes: row.likes, status: "missing" as const })),
		...data.waiting.map((row) => ({ id: row.clusterId, question: row.question, askedBy: row.askedBy, likes: row.likes, status: "waiting" as const })),
		...data.declined.map((row) => ({ id: row.clusterId, question: row.question, askedBy: row.askedBy, likes: row.likes, status: "declined" as const })),
	].sort((x, y) => y.askedBy - x.askedBy || y.likes - x.likes);
	const published = data.answered.filter((row) => row.published).length;

	return (
		<div className="sm-report">
			<div className="sm-report__top">
				<h2 className="sm-report__title">회차 리포트</h2>
				<button type="button" className="button button--small sm-actions--end" onClick={copy}>
					{copied ? "복사했습니다" : "글로 복사"}
				</button>
			</div>

			{/* 숫자 여섯(개선안 F1, 2026-10-24). 예전엔 이 숫자들이 한 문장 안에 묻혀 있었다. */}
			<div className="sm-kpis">
				<Kpi label="받은 질문" value={summary.questions} sub={`묶음 ${summary.clusters}개로`} />
				<Kpi
					label="답한 묶음"
					value={summary.answered}
					sub={`발행 ${published} · 미발행 ${summary.answered - published}`}
					tone="good"
				/>
				<Kpi
					label="아직 안 한 것"
					value={summary.waiting}
					sub={summary.unclustered > 0 ? `[집계] 안 한 질문 ${summary.unclustered}개 따로` : "[답하기] 전"}
					tone={summary.waiting > 0 ? "todo" : undefined}
				/>
				{/* 🔴 리포트의 본체. 답한 것보다 답하지 못한 것이 다음 회차에 쓸 값이다. */}
				<Kpi label="영상에 답 없음" value={summary.missing} sub="다음 설명회 큐시트" tone={summary.missing > 0 ? "bad" : undefined} />
				<Kpi
					label="질문 하나당"
					value={cost.perQuestionKrw === null ? "-" : won(cost.perQuestionKrw)}
					sub={`답변 ${cost.attempted}건 ${won(cost.answer.krw)}`}
				/>
				<Kpi label="회차 준비" value={won(cost.prepare.krw)} sub="영상당 한 번" />
			</div>

			{/* 막대표 — 무엇을 많이 물었고 각각 어디까지 왔나. */}
			{ranked.length > 0 && <AskedChart rows={ranked} />}

			<div className="sm-report__cols">
				<div className="sm-report__col">
					{/* 🔴 본체가 먼저다. 답한 것보다 **답하지 못한 것**이 이 리포트의 값이다 —
					    다음 회차에 무엇을 말해야 하는지가 여기 있다. */}
					<Section
						title="이 영상이 답하지 않은 질문"
						hint="다음 설명회 큐시트이자, 공고에서 빠진 정보입니다."
						rows={data.missing}
						empty="시청자가 물어본 것에 이 영상이 전부 답했습니다."
					/>
					{data.waiting.length > 0 && (
						<Section
							title="할 일 — 아직 답하기를 안 누른 질문"
							hint="영상에 답이 있는지 아직 확인하지 않았습니다. 많이 물은 순입니다."
							rows={[...data.waiting].sort((x, y) => y.askedBy - x.askedBy || y.likes - x.likes)}
							empty=""
							sourceId={sourceId}
						/>
					)}
					{data.declined.length > 0 && (
						<Section
							title="만들었다가 물린 질문"
							hint="답은 영상에 있습니다. 다시 만들면 다른 후보를 고를 수 있습니다."
							rows={data.declined}
							empty=""
						/>
					)}
				</div>

				<div className="sm-report__col">
					<section className="sm-report__card">
						<h3 className="sm-report__head">
							답한 질문 <span className="sm-meta">{data.answered.length}개</span>
						</h3>
						{data.answered.length === 0 ? (
							<p className="sm-meta">아직 없습니다.</p>
						) : (
							<ul className="sm-report__answered">
								{data.answered.map((row) => (
									<li key={row.clusterId}>
										<span className="sm-report__aq">{row.question}</span>
										<span className="sm-meta">
											{row.askedBy}명이 물음
											{row.totalSec !== null && ` · ${Math.round(row.totalSec)}초`}
											{row.parts > 1 && ` · ${row.parts}조각을 이어붙임`}
										</span>
										<span className={`sm-report__pub${row.published ? " sm-report__pub--on" : ""}`}>
											{row.published ? "발행됨" : "미발행"}
										</span>
									</li>
								))}
							</ul>
						)}
					</section>

					<section className="sm-report__card">
						<h3 className="sm-report__head">비용</h3>
						{/* 🔴 회차 준비와 질문 답변을 가른다. 합쳐서 질문 수로 나누면 질문이 하나일 때
						    전사비가 통째로 그 질문에 얹힌다. */}
						<table className="table sm-report__cost">
							<tbody>
								<tr>
									<td>
										회차 준비 <span className="sm-meta">영상당 한 번</span>
									</td>
									<td>{won(cost.prepare.krw)}</td>
								</tr>
								<tr>
									<td>
										질문 답변 <span className="sm-meta">{cost.attempted}건 합계</span>
									</td>
									<td>{won(cost.answer.krw)}</td>
								</tr>
								<tr>
									<td>
										<strong>질문 하나당</strong>
									</td>
									<td>
										<strong>{cost.perQuestionKrw === null ? "-" : won(cost.perQuestionKrw)}</strong>
									</td>
								</tr>
							</tbody>
						</table>
						<p className="sm-meta sm-report__note">
							{cost.note} · {cost.prepare.rate.model} 단가 기준 추정입니다.
						</p>
					</section>
				</div>
			</div>
		</div>
	);
}

type ChartStatus = "answered" | "missing" | "waiting" | "declined";

interface ChartRow {
	id: number;
	question: string;
	askedBy: number;
	likes: number;
	status: ChartStatus;
}

// 🔴 상태는 색만으로 말하지 않는다 — 막대 옆에 글자 딱지가 같이 붙는다(색을 못 가르는 사람·흑백 인쇄).
const STATUS_TEXT: Record<ChartStatus, string> = {
	answered: "답함",
	missing: "영상에 없음",
	waiting: "아직",
	declined: "물림",
};

// 막대표에 올리는 줄 수. 한 명이 물은 질문까지 다 올리면 막대가 다 같은 길이라 말하는 게 없다.
const CHART_ROWS = 8;

/**
 * 가장 많이 물은 질문 — 가로 막대(개선안 F2 의 표). 길이는 **질문한 사람 수**다.
 *
 * 🔴 좋아요가 아니라 사람 수로 줄을 세운다. 좋아요는 목소리 큰 소수가 순위를 끈다(Canny 같은 투표 보드의
 * 오래된 비판). 좋아요는 옆에 숫자로만 둔다. 막대 색은 그 질문이 어디까지 왔는지(답함·영상에 없음·아직)다.
 */
function AskedChart({ rows }: { rows: ChartRow[] }) {
	const shown = rows.slice(0, CHART_ROWS);
	const max = Math.max(1, ...shown.map((row) => row.askedBy));
	const present = (Object.keys(STATUS_TEXT) as ChartStatus[]).filter((key) => shown.some((row) => row.status === key));
	return (
		<section className="sm-report__card" aria-label="가장 많이 물은 질문">
			<div className="sm-chart__head">
				<h3 className="sm-report__head">가장 많이 물은 질문</h3>
				<span className="sm-meta">막대 = 질문한 사람 수</span>
				<ul className="sm-chart__legend">
					{present.map((key) => (
						<li key={key}>
							<span className={`sm-chart__swatch sm-chart__swatch--${key}`} aria-hidden="true" />
							{STATUS_TEXT[key]}
						</li>
					))}
				</ul>
			</div>
			<ol className="sm-chart">
				{shown.map((row) => (
					<li
						key={row.id}
						className="sm-chart__row"
						title={`${row.question} — ${row.askedBy}명 · 좋아요 ${row.likes} · ${STATUS_TEXT[row.status]}`}
					>
						<span className="sm-chart__label">{row.question}</span>
						<span className="sm-chart__track">
							<span
								className={`sm-chart__bar sm-chart__bar--${row.status}`}
								style={{ width: `${(row.askedBy / max) * 100}%` }}
							/>
						</span>
						<span className="sm-chart__value">{row.askedBy}명</span>
						<span className={`sm-chart__tag sm-chart__tag--${row.status}`}>{STATUS_TEXT[row.status]}</span>
					</li>
				))}
			</ol>
			{rows.length > CHART_ROWS && (
				<p className="sm-meta sm-report__note">
					나머지 {rows.length - CHART_ROWS}개는 아래 목록에 있습니다.
				</p>
			)}
		</section>
	);
}

function Kpi({
	label,
	value,
	sub,
	tone,
}: {
	label: string;
	value: number | string;
	sub: string;
	tone?: "good" | "bad" | "todo";
}) {
	return (
		<div className={`sm-kpi${tone ? ` sm-kpi--${tone}` : ""}`}>
			<span className="sm-kpi__label">{label}</span>
			<span className="sm-kpi__value">{value}</span>
			<span className="sm-kpi__sub">{sub}</span>
		</div>
	);
}

function Section({
	title,
	hint,
	rows,
	empty,
	sourceId,
}: {
	title: string;
	hint: string;
	rows: ShortsReportQuestion[];
	empty: string;
	/** 주면 줄마다 "답하러 가기" 를 단다 — 할 일 목록에서 바로 질문 탭의 그 질문으로 간다. */
	sourceId?: number;
}) {
	return (
		<section className="sm-report__card">
			<h3 className="sm-report__head">
				{title} <span className="sm-meta">{rows.length}개</span>
			</h3>
			<p className="sm-meta sm-report__hint">{hint}</p>
			{rows.length === 0 ? (
				<p className="sm-meta">{empty}</p>
			) : (
				<ol className="sm-report__missing">
					{rows.map((row) => (
						<li key={row.clusterId}>
							<p className="sm-report__q">
								{row.question}
								<span className="sm-meta"> · {row.askedBy}명이 물음</span>
								{row.likes > 0 && <span className="sm-meta"> · 좋아요 {row.likes}</span>}
								{sourceId !== undefined && (
									<Link
										className="sm-report__go"
										to={`/studio/sources/${sourceId}/questions?cluster=${row.clusterId}`}
									>
										답하러 가기 →
									</Link>
								)}
							</p>
							{/* 🔴 이유가 이 목록의 값이다. "답할 구간 없음" 딱지만으로는 영상이 정말
							    안 다룬 건지 검색이 빗나간 건지 구분할 수 없다. */}
							{row.reason && <p className="sm-report__why">{row.reason}</p>}
							{row.error && <p className="sm-report__why sm-cluster__note--ng">{row.error}</p>}
							{row.texts.length > 1 && (
								<details className="sm-report__texts">
									<summary>원문 {row.texts.length}개</summary>
									<ul className="sm-report__list">
										{row.texts.map((text, n) => (
											<li key={n}>{text}</li>
										))}
									</ul>
								</details>
							)}
						</li>
					))}
				</ol>
			)}
		</section>
	);
}

function won(value: number): string {
	return `${value.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}원`;
}

/**
 * 붙여 넣을 수 있는 글.
 *
 * 🔴 **이게 산출물이다.** 화면은 보는 것이고, 담당자가 윗선에 보내는 것은 이 글이다.
 * 마크다운 문법을 쓰지 않는다 — 메일과 메신저에 그대로 붙었을 때 기호가 보이면 안 된다.
 */
function asText(data: ShortsReport): string {
	const { summary, cost } = data;
	const lines: string[] = [
		`${data.source.title} — 회차 리포트`,
		"",
		`질문 ${summary.questions}개 · 묶음 ${summary.clusters}개`,
		`답한 것 ${summary.answered} · 이 영상에 없는 것 ${summary.missing} · 아직 안 한 것 ${summary.waiting}`,
		"",
		`[이 영상이 답하지 않은 질문 ${data.missing.length}개]`,
	];
	if (data.missing.length === 0) {
		lines.push("  없습니다.");
	}
	data.missing.forEach((row, n) => {
		lines.push(`${n + 1}. ${row.question} (${row.askedBy}명이 물음)`);
		if (row.reason) {
			lines.push(`   판정: ${row.reason}`);
		}
	});
	lines.push("", `[답한 질문 ${data.answered.length}개]`);
	if (data.answered.length === 0) {
		lines.push("  없습니다.");
	}
	data.answered.forEach((row, n) => {
		const length = row.totalSec === null ? "" : ` · ${Math.round(row.totalSec)}초`;
		lines.push(`${n + 1}. ${row.question}${length} · ${row.published ? "발행됨" : "미발행"}`);
	});
	lines.push(
		"",
		"[비용]",
		`  회차 준비 ${won(cost.prepare.krw)} (영상당 한 번)`,
		`  질문 답변 ${won(cost.answer.krw)} (${cost.attempted}건)`,
		`  질문 하나당 ${cost.perQuestionKrw === null ? "-" : won(cost.perQuestionKrw)}`,
		`  ${cost.note}`,
	);
	return lines.join("\n");
}
