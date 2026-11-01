import { Link } from "react-router-dom";
import { fetchYoutubeStats, refreshYoutubeStats } from "../api";
import { useAsync } from "../../shared/useAsync";
import type { YoutubeShort, YoutubeStatsView } from "../types";
import type { SourceView } from "../SourcePage";

/**
 * 유튜브 성과 — 이 영상에서 올린 숏폼이 유튜브에서 어떻게 되고 있나(tease §7-6, 2026-11-01).
 *
 * 🔴 **우리 시청자 퍼널(공개 탭의 재생·CTA 숫자)과 섞지 않는다.** 시청자 페이지는 없어질 수 있다 — 그래서 탭도
 * 데이터도 따로다. 두 숫자를 더하거나 한 표에 나란히 두지 않는다.
 *
 * 두 층이 늦는 정도가 달라 칸을 가른다 — **지금 수치**(바로 반영)와 **분석**(2~3일 늦음). 분석이 비어 있으면 0 이
 * 아니라 "—" 다. 올린 지 이틀이 안 된 숏폼은 그게 정상이다.
 *
 * 화면을 열 때 유튜브를 부르지 않는다(서버가 DB 만 읽는다). [성과 가져오기] 를 눌러야 새로 읽는다.
 */
export function YoutubeStatsTab({ view }: { view: SourceView }) {
	const { sourceId, submit, jobBusy, sourceBusy } = view;
	const busy = jobBusy || sourceBusy;
	const stats = useAsync<YoutubeStatsView>(
		// reloadToken — [성과 가져오기] 잡이 끝나면 다시 읽는다.
		() => fetchYoutubeStats(sourceId), [sourceId, view.reloadToken], { keepPrevious: true, subject: sourceId },
	);

	if (stats.loading) return <p className="state">불러오는 중…</p>;
	if (stats.error || !stats.data) return <p className="state state--error">유튜브 성과를 읽지 못했습니다.</p>;
	const data = stats.data;
	const { totals, channel } = data;

	return (
		<div className="sm-report">
			<div className="sm-report__top">
				<h2 className="sm-report__title">유튜브 성과</h2>
				{data.shorts.length > 0 && (
					<button
						type="button"
						className="button button--small sm-actions--end"
						disabled={busy}
						title={busy ? "다른 작업이 끝나야 실행할 수 있습니다" : "올린 숏폼의 지금 수치와 분석을 유튜브에서 다시 읽습니다"}
						onClick={() => submit(() => refreshYoutubeStats(sourceId))}
					>
						성과 가져오기
					</button>
				)}
			</div>

			<p className="sm-meta sm-report__hint">
				{data.fetchedAt ? `마지막으로 가져온 때 ${dateTime(data.fetchedAt)}. ` : "아직 가져오지 않았습니다. "}
				지금 수치는 바로 반영되고, 분석 수치는 유튜브가 2~3일 뒤에 냅니다
				{data.through && ` — 지금은 ${data.through}(태평양 시간)까지`}. 이 탭은 우리 시청자 페이지의 숫자와 따로 셉니다.
			</p>
			{channel.connected && !channel.canAnalytics && (
				<p className="sm-ytstats__warn">
					연결할 때 성과(분석) 권한을 받지 못했습니다. <Link to="/studio">스튜디오 홈</Link>에서 유튜브를 끊고 다시 연결한 뒤
					동의 화면의 권한을 모두 체크하세요.
				</p>
			)}
			{!channel.connected && data.shorts.length > 0 && (
				<p className="sm-ytstats__warn">
					유튜브 채널이 연결돼 있지 않습니다. <Link to="/studio">스튜디오 홈</Link>에서 먼저 연결하세요.
				</p>
			)}

			{data.shorts.length === 0 ? (
				<section className="sm-report__card">
					<p className="sm-meta">
						이 영상에서 유튜브에 올린 숏폼이 없습니다. <Link to={`/studio/sources/${sourceId}/clips`}>공개 탭</Link>에서
						[유튜브에 올리기] 를 하면 여기 모입니다.
					</p>
				</section>
			) : (
				<>
					{totals && (
						<div className="sm-kpis">
							<Kpi label="조회수" value={num(totals.viewCount)} sub={`숏폼 ${totals.shorts}개 · 지금 수치`} />
							<Kpi label="좋아요" value={num(totals.likeCount)} sub="지금 수치" />
							<Kpi label="댓글" value={num(totals.commentCount)} sub="지금 수치" />
							<Kpi label="참여 조회" value={num(totals.engagedViews)} sub="분석 · 첫 프레임을 넘겨 본 것" />
							<Kpi label="시청 시간" value={minutes(totals.minutesWatched)} sub="분석" />
							<Kpi label="구독" value={num(totals.subscribersGained)} sub={`분석 · 공유 ${num(totals.shares)}`} />
						</div>
					)}

					<section className="sm-report__card">
						<h3 className="sm-report__head">
							올린 숏폼 <span className="sm-meta">{data.shorts.length}개</span>
						</h3>
						<div className="table-wrap">
							<table className="table sm-ytstats__table">
								<thead>
									<tr>
										<th>숏폼</th>
										<th title="Data API · 바로 반영. 재생이 시작될 때마다 센다">조회수</th>
										<th>좋아요</th>
										<th>댓글</th>
										<th title="분석 · 첫 프레임을 넘겨 본 조회(예전 Shorts 조회 방식)">참여 조회</th>
										<th title="분석 · 참여 조회 기준 평균">평균 시청</th>
										<th>공유</th>
										<th>구독</th>
										<th title="분석 · 날짜별 조회(태평양 시간)">흐름</th>
									</tr>
								</thead>
								<tbody>
									{data.shorts.map((short) => (
										<Row key={short.uploadId} short={short} />
									))}
								</tbody>
							</table>
						</div>
						<p className="sm-meta sm-report__note">
							조회수는 유튜브 화면에 보이는 숫자와 같습니다(재생이 시작될 때마다 셉니다). 참여 조회는 첫 프레임을 넘겨 본
							것입니다. &quot;—&quot; 는 아직 분석이 나오지 않은 것입니다.
						</p>
						{/* 🔴 정직하게 적는다. 숏폼에서 원본으로 넘어간 수는 API 로 숏폼별로 읽을 수 없다(tease §7-6). */}
						<p className="sm-meta sm-report__note">
							숏폼에서 원본 영상으로 넘어간 수는 유튜브 API 가 숏폼별로 주지 않습니다. 원본으로 가는 링크(관련 동영상)는
							각 숏폼의 &quot;스튜디오&quot; 에서 손으로 겁니다.
						</p>
					</section>
				</>
			)}
		</div>
	);
}

function Row({ short }: { short: YoutubeShort }) {
	const s = short.stats;
	const note = short.otherChannel
		? "다른 채널에 올림 — 지금 연결로는 읽을 수 없습니다"
		: !s
			? "아직 가져오지 않음"
			: !s.found
				? "유튜브에서 찾을 수 없음(지웠거나 볼 수 없음)"
				: null;
	return (
		<tr>
			<td className="sm-ytstats__title">
				<a href={short.shortsUrl} target="_blank" rel="noreferrer">
					{short.title}
				</a>
				<span className="sm-meta">
					{" "}
					· {date(short.uploadedAt)} · {PRIVACY[s?.privacy ?? short.uploadedPrivacy] ?? s?.privacy ?? short.uploadedPrivacy} ·{" "}
					<a href={short.studioUrl} target="_blank" rel="noreferrer">
						스튜디오
					</a>
				</span>
				{note && <span className="sm-ytstats__note">{note}</span>}
			</td>
			<td>{num(s?.viewCount)}</td>
			<td>{num(s?.likeCount)}</td>
			<td>{num(s?.commentCount)}</td>
			<td>{num(s?.engagedViews)}</td>
			<td>
				{s?.avgViewSec == null ? "—" : `${Math.round(s.avgViewSec)}초`}
				{s?.avgViewPct != null && <span className="sm-meta"> · {Math.round(s.avgViewPct)}%</span>}
			</td>
			<td>{num(s?.shares)}</td>
			<td>{num(s?.subscribersGained)}</td>
			<td>
				<Spark days={short.daily} />
			</td>
		</tr>
	);
}

/** 날짜별 조회 막대. 숫자는 마우스를 올리면 보인다 — 칸이 좁아 막대 길이로만 흐름을 말한다. */
function Spark({ days }: { days: YoutubeShort["daily"] }) {
	if (days.length === 0) return <span className="sm-meta">—</span>;
	const max = Math.max(1, ...days.map((d) => d.views ?? 0));
	return (
		<span className="sm-ytstats__spark" aria-label={`날짜별 조회 ${days.length}일`}>
			{days.map((d) => (
				<span
					key={d.day}
					className="sm-ytstats__bar"
					style={{ height: `${Math.max(2, ((d.views ?? 0) / max) * 100)}%` }}
					title={`${d.day} · 조회 ${num(d.views)} · 참여 ${num(d.engagedViews)}`}
				/>
			))}
		</span>
	);
}

function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
	return (
		<div className="sm-kpi">
			<span className="sm-kpi__label">{label}</span>
			<span className="sm-kpi__value">{value}</span>
			<span className="sm-kpi__sub">{sub}</span>
		</div>
	);
}

const PRIVACY: Record<string, string> = { private: "비공개", unlisted: "일부 공개", public: "공개" };

/** 🔴 없음(null)은 "—". 0 으로 보이면 "아무도 안 봤다" 로 읽힌다. */
function num(value: number | null | undefined): string {
	return value == null ? "—" : value.toLocaleString("ko-KR");
}

function minutes(value: number | null | undefined): string {
	if (value == null) return "—";
	return value >= 60 ? `${(value / 60).toFixed(1)}시간` : `${Math.round(value)}분`;
}

function date(iso: string): string {
	return new Date(iso).toLocaleDateString("ko-KR");
}

function dateTime(iso: string): string {
	return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}
