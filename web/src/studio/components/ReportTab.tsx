import { useState } from "react";
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

	return (
		<>
			<div className="sm-actions" style={{ justifyContent: "space-between" }}>
				<h2 className="section-title" style={{ margin: 0 }}>회차 리포트</h2>
				<button type="button" className="button button--small" onClick={copy}>
					{copied ? "복사했습니다" : "글로 복사"}
				</button>
			</div>

			<p className="sm-report__lead">
				질문 {summary.questions}개가 {summary.clusters}개 묶음으로 모였습니다. 답한 것{" "}
				{summary.answered} · <strong>이 영상에 없는 것 {summary.missing}</strong> · 아직 안 한 것{" "}
				{summary.waiting}
				{summary.declined > 0 && ` · 물린 것 ${summary.declined}`}
				{summary.unclustered > 0 && (
					<>
						{" "}
						<span className="sm-meta">([집계] 안 한 질문 {summary.unclustered}개)</span>
					</>
				)}
			</p>

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
					title="아직 답하기를 안 누른 질문"
					hint="영상에 답이 있는지 아직 확인하지 않았습니다. 질문 탭에서 [답하기]를 누르세요."
					rows={data.waiting}
					empty=""
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

			<h3 className="sm-report__head">답한 질문 {data.answered.length}개</h3>
			{data.answered.length === 0 ? (
				<p className="sm-meta">아직 없습니다.</p>
			) : (
				<ul className="sm-report__list">
					{data.answered.map((row) => (
						<li key={row.clusterId}>
							{row.question}
							<span className="sm-meta">
								{" "}
								· {row.askedBy}명이 물음
								{row.totalSec !== null && ` · ${Math.round(row.totalSec)}초`}
								{row.parts > 1 && ` · ${row.parts}조각을 이어붙임`}
								{" · "}
								{row.published ? "발행됨" : "미발행"}
							</span>
						</li>
					))}
				</ul>
			)}

			<h3 className="sm-report__head">비용</h3>
			{/* 🔴 회차 준비와 질문 답변을 가른다. 합쳐서 질문 수로 나누면 질문이 하나일 때
			    전사비가 통째로 그 질문에 얹힌다. */}
			<div className="table-wrap">
				<table className="table">
					<tbody>
						<tr>
							<td>회차 준비 <span className="sm-meta">영상당 한 번</span></td>
							<td>{won(cost.prepare.krw)}</td>
						</tr>
						<tr>
							<td>질문 답변 <span className="sm-meta">{cost.attempted}건 합계</span></td>
							<td>{won(cost.answer.krw)}</td>
						</tr>
						<tr>
							<td><strong>질문 하나당</strong></td>
							<td>
								<strong>
									{cost.perQuestionKrw === null ? "-" : won(cost.perQuestionKrw)}
								</strong>
							</td>
						</tr>
					</tbody>
				</table>
			</div>
			<p className="sm-meta">
				{cost.note} · {cost.prepare.rate.model} 단가 기준 추정입니다.
			</p>
		</>
	);
}

function Section({
	title,
	hint,
	rows,
	empty,
}: {
	title: string;
	hint: string;
	rows: ShortsReportQuestion[];
	empty: string;
}) {
	return (
		<>
			<h3 className="sm-report__head">
				{title} <span className="sm-meta">{rows.length}개</span>
			</h3>
			<p className="sm-meta">{hint}</p>
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
							</p>
							{/* 🔴 이유가 이 목록의 값이다. "답할 구간 없음" 딱지만으로는 영상이 정말
							    안 다룬 건지 검색이 빗나간 건지 구분할 수 없다. */}
							{row.reason && <p className="sm-report__why">{row.reason}</p>}
							{row.error && <p className="sm-report__why sm-cluster__note--ng">{row.error}</p>}
							{row.texts.length > 1 && (
								<details className="sm-fold">
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
		</>
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
