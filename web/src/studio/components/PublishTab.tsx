import { useState } from "react";
import { Link } from "react-router-dom";
import { clipUrl, fetchInsights, patchClipTitle, publishClip, renderClip, reviewClip, uploadClipToYoutube } from "../api";
import { Modal } from "../../shared/Modal";
import { useAsync } from "../../shared/useAsync";
import { time } from "../../shared/format";
import type { ShortsClip, ShortsFunnelRow, ShortsInsights } from "../types";
import type { SourceView } from "../SourcePage";
import { YoutubeUploadLine, YoutubeUploadModal } from "./YoutubeUploadModal";
import "../clips.css";

// 거르개. "공개" 탭이지만 안에는 아직 안 내보낸 클립도 있다 — 둘을 한 번에 가를 수 있어야
// 탭 이름이 거짓말이 되지 않는다.
const FILTERS: { key: string; label: string; match: (clip: ShortsClip) => boolean }[] = [
	{ key: "all", label: "전체", match: () => true },
	{ key: "live", label: "공개 중", match: (clip) => clip.published_at !== null },
	{ key: "hidden", label: "비공개", match: (clip) => clip.published_at === null },
];

// 고를 답 칸에 적는 질문 수. 넘치면 "외 N개" 로 질문 탭에 넘긴다 — 칸이 영상보다 길어지지 않게.
const WAITING_SHOWN = 5;

/**
 * 공개 탭 — 만들어진 클립 전부와, 시청자에게 내보내는 자리.
 *
 * 🔴 예전 "클립" 탭은 이 목록과 "직접 골라 만들기"(5~7단계)를 한 화면에 쌓았고, 만들기는 접힌 채
 * 맨 아래에 있었다. 하는 일이 다르다 — 이쪽은 **이미 만든 것을 보고 내보내는** 일이고, 저쪽은
 * **새로 뽑는** 일이다. 그래서 탭을 갈랐다(새 클립 탭, 2026-09-14).
 *
 * 세로 숏폼 선반(개선안 E1, 2026-10-24). 숏폼은 9:16 이라 예전의 가로 카드(영상 왼쪽 · 글 오른쪽)는
 * 카드 하나가 폭의 절반을 먹고 영상은 작았다. 영상을 카드 위에 세우고 여러 열로 늘어놓는다 —
 * 시청자가 보는 모양 그대로다. 아직 고르지 않은 답(검토 대기)은 맨 끝 한 칸에 모아 둔다.
 */
export function PublishTab({ view }: { view: SourceView }) {
	const [filter, setFilter] = useState("all");
	const { data, clusters, sourceId } = view;
	// 클립을 새로 내보내거나 내리면 반응 표도 다시 읽는다. 페이지의 reloadToken 은 탭까지 내려오지 않아서
	// 클립 목록의 모양(무엇이 공개 중인가)을 열쇠로 쓴다.
	const reloadKey = (data?.clips ?? []).map((c) => `${c.id}${c.published_at ? "+" : ""}`).join(",");
	// 시청자 반응(재생·완주·원본 이동). 기록 탭의 표와 같은 값이다 — 내보낸 클립 옆에 붙어 있어야
	// "무엇을 더 내보낼까" 를 이 자리에서 정할 수 있다.
	const insights = useAsync<ShortsInsights>(() => fetchInsights(sourceId), [sourceId, reloadKey], {
		keepPrevious: true,
		subject: sourceId,
	});
	if (!data) {
		return null;
	}

	const clips = data.clips;
	const active = FILTERS.find((f) => f.key === filter) ?? FILTERS[0];
	// 순서는 서버가 준 그대로(최신 먼저) — 방금 만든 것이 위에 있어야 한다.
	const visible = clips.filter(active.match);
	const liveCount = clips.filter((clip) => clip.published_at !== null).length;
	const stats = new Map<number, ShortsFunnelRow>((insights.data?.clips ?? []).map((row) => [row.clip_id, row]));
	// 후보가 나왔는데 아직 하나를 고르지 않은 질문. 비공개 클립과 같은 "아직 안 나간 것" 이라 전체·비공개에서 보인다.
	const waiting =
		filter === "live" ? [] : (clusters.data?.clusters ?? []).filter((c) => c.status === "REVIEW" && c.clip === null);

	return (
		<>
			<div className="sm-shorts__head">
				<h2 className="sm-shorts__title">완성 클립</h2>
				<span className="sm-meta">
					{clips.length}개 · 시청자에게 공개 중 {liveCount}개
					{waiting.length > 0 && ` · 고를 답 ${waiting.length}개`}
				</span>
				<span className="sm-actions sm-actions--end">
					{clips.length > 0 && (
						<span className="sm-chips">
							{FILTERS.map((f) => (
								<button
									key={f.key}
									type="button"
									aria-pressed={filter === f.key}
									className={`sm-chip${filter === f.key ? " sm-chip--on" : ""}`}
									onClick={() => setFilter(f.key)}
								>
									{f.label} <span className="sm-chip__n">{clips.filter(f.match).length}</span>
								</button>
							))}
						</span>
					)}
					{data.source.published && (
						<a className="button button--small" href={`/watch/${data.source.id}`} target="_blank" rel="noreferrer">
							시청자 화면 열기 ↗
						</a>
					)}
				</span>
			</div>

			{clips.length === 0 && waiting.length === 0 && (
				<p className="sm-shorts__empty">
					아직 만든 클립이 없습니다.{" "}
					<Link to={`/studio/sources/${data.source.id}/questions`}>질문</Link> 탭에서 시청자 질문에
					답하거나, <Link to={`/studio/sources/${data.source.id}/make`}>새 클립</Link> 탭에서 기준을 적어
					직접 만드세요.
				</p>
			)}
			{clips.length > 0 && visible.length === 0 && waiting.length === 0 && (
				<p className="sm-shorts__empty">여기에 해당하는 클립이 없습니다</p>
			)}

			<div className="sm-shorts">
				{visible.map((clip) => (
					<ShortCard key={clip.id} clip={clip} view={view} stats={stats.get(clip.id) ?? null} />
				))}
				{/* 고를 답은 **한 칸**에 모은다. 질문마다 칸을 하나씩 세웠더니(첫 판) 영상 없는 빈 칸이 클립
				    사이를 채워 선반이 반쯤 비어 보였다(2026-10-24 사용자 피드백). 다섯 개까지 적고, 하나를
				    누르면 질문 탭의 그 질문으로 간다. */}
				{waiting.length > 0 && (
					<section className="sm-short sm-short--waiting" aria-label={`고를 답 ${waiting.length}개`}>
						<span className="sm-short__plus" aria-hidden="true">
							<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
								<path d="M12 5v14M5 12h14" />
							</svg>
						</span>
						<ul className="sm-short__waitlist">
							{waiting.slice(0, WAITING_SHOWN).map((cluster) => (
								<li key={cluster.id}>
									<Link
										className="sm-short__waititem"
										to={`/studio/sources/${data.source.id}/questions?cluster=${cluster.id}`}
									>
										<span className="sm-short__waitq" title={cluster.canonical_text}>
											{cluster.canonical_text}
										</span>
										<span className="sm-short__waitgo">추가하러 가기 →</span>
									</Link>
								</li>
							))}
						</ul>
						{waiting.length > WAITING_SHOWN && (
							<Link className="sm-short__waitmore" to={`/studio/sources/${data.source.id}/questions`}>
								외 {waiting.length - WAITING_SHOWN}개 — 질문 탭에서 보기
							</Link>
						)}
					</section>
				)}
			</div>
		</>
	);
}

/** 숏폼 하나 — 위에 9:16 영상, 아래에 무엇의 답인지 · 제목 · 반응 · 공개. */
function ShortCard({ clip, view, stats }: { clip: ShortsClip; view: SourceView; stats: ShortsFunnelRow | null }) {
	const { jobBusy, submit, act, reload } = view;
	const [editing, setEditing] = useState(false);
	const [uploading, setUploading] = useState(false);
	const live = clip.published_at !== null;
	const envelope = clip.end_sec - clip.start_sec;
	// 🔴 길이는 total_sec 이 먼저다. 조합 클립의 start~end 는 조각들을 감싼 봉투라서, 그걸 빼면
	// 18초짜리 클립이 "1186초" 로 찍힌다(2026-09-14 화면에서 잡았다).
	const seconds = Math.round(clip.total_sec ?? envelope);
	const stitched = clip.total_sec !== null && envelope - clip.total_sec > 1;
	// 🔴 사람 평가와 모델 판정이 한 목록에 최신순으로 온다. 지금 내 평가는 사람 행의 첫 줄이다 —
	// 전부 세면 모델 판정이 사람 표처럼 읽힌다. 서버가 연타를 한 줄로 막으므로 셀 것도 없다.
	const myVerdict = clip.reviews.find((r) => r.reviewer === "human")?.verdict ?? null;
	const title = clip.title ?? `클립 ${clip.id}`;

	return (
		<article className={`sm-short${live ? " sm-short--live" : ""}`}>
			<div className="sm-short__media">
				{clip.rendered ? (
					// 🔴 비율을 미리 잡는다(CSS). 메타데이터가 오기 전엔 <video> 높이가 0 이라 카드가 납작하게
					// 그려졌다가 영상이 뜨면서 격자 전체가 아래로 밀린다.
					<video controls preload="metadata" src={clipUrl(clip.id)} />
				) : (
					<div className="sm-short__pending">
						<span>아직 렌더 전</span>
						<button
							type="button"
							className="button button--small sm-go"
							disabled={jobBusy}
							onClick={() => submit(() => renderClip(clip.id))}
						>
							렌더하기
						</button>
					</div>
				)}
				<span className={`sm-short__live${live ? " sm-short__live--on" : ""}`}>{live ? "공개 중" : "비공개"}</span>
				{clip.score !== null && <span className="sm-short__score">{clip.score}</span>}
			</div>

			<div className="sm-short__body">
				{/* 🔴 이 클립이 무엇의 답인가. 시청자 질문에서 나온 것과 크리에이터가 자기 기준으로 뽑은
				    것이 한 격자에 섞이므로, "클립 3" 이라는 이름만으로는 알 수 없는 걸 첫 줄에서 가른다. */}
				<div className="sm-short__origin">
					{clip.question ? (
						<>
							<span className="sm-kind">시청자 질문</span>
							{clip.asked_by > 0 && <span>{clip.asked_by}명</span>}
						</>
					) : (
						<>
							<span className="sm-kind sm-kind--own">내 기준</span>
							<span className="sm-short__criteria" title={clip.criteria_prompt ?? undefined}>
								{clip.criteria_prompt ?? "기준 없음"}
							</span>
						</>
					)}
				</div>

				<h3 className="sm-short__title" title={title}>
					{title}
				</h3>
				{/* 제목을 고쳤으면 원래 무엇을 물었는지가 제목에서 사라진다. 같으면 두 번 쓰지 않는다. */}
				{clip.question && clip.question !== title && <p className="sm-short__question">Q. {clip.question}</p>}

				<span className="sm-short__meta">
					{seconds}초 · {time(clip.start_sec)}
					{stitched ? " 외 이어붙임" : `–${time(clip.end_sec)}`}
				</span>

				{/* 내보낸 클립만 반응이 있다. 0 도 그대로 적는다 — 비어 있으면 "아직 안 셌나" 로 읽힌다. */}
				{live && (
					<dl className="sm-short__stats">
						<div>
							<dt>재생</dt>
							<dd>{stats?.short_play ?? 0}</dd>
						</div>
						<div>
							<dt>완주</dt>
							<dd>{stats?.short_complete ?? 0}</dd>
						</div>
						<div>
							<dt>원본으로</dt>
							<dd>{stats?.origin_play ?? 0}</dd>
						</div>
					</dl>
				)}

				<div className="sm-short__foot">
					{/* 🔴 질문에 답한 클립은 질문 패널에서 발행하는 게 주된 길이다(클러스터 상태가 함께
					    움직여야 하므로). 여기 있는 건 크리에이터가 자기 기준으로 뽑은 클립에도 시청자에게
					    보여줄 길이 있어야 해서 둔 것이다. */}
					{clip.rendered && (
						<div className="sm-short__sends">
							<button
								type="button"
								className={`button button--small sm-short__publish${live ? "" : " sm-go"}`}
								disabled={jobBusy}
								onClick={() =>
									act(async () => {
										await publishClip(clip.id, !clip.published_at);
										reload();
									})
								}
							>
								{live ? "내리기" : "시청자에게 공개"}
							</button>
							{/* 🔴 시청자 공개(우리 페이지)와 유튜브 업로드(남의 채널)는 다른 일이다 — 한 버튼에 묶지
							    않는다. 여는 것까지는 보기 전용도 된다. 막는 건 대화상자 안의 [올리기] 다. */}
							<button
								type="button"
								className="button button--small"
								disabled={jobBusy}
								onClick={() => setUploading(true)}
							>
								유튜브에 올리기
							</button>
						</div>
					)}
					{clip.uploads.length > 0 && <YoutubeUploadLine upload={clip.uploads[0]} />}
					<div className="sm-short__tools">
						{/* 누른 쪽이 눌린 채로 보여야 한다 — 안 그러면 들어갔는지 몰라 다시 누른다(연타의 출발점). */}
						<button
							type="button"
							className="sm-short__tool"
							aria-pressed={myVerdict === "OK"}
							onClick={() => act(() => reviewClip(clip, "OK"))}
						>
							쓸만함
						</button>
						<button
							type="button"
							className="sm-short__tool sm-short__tool--ng"
							aria-pressed={myVerdict === "NG"}
							onClick={() => act(() => reviewClip(clip, "NG"))}
						>
							아님
						</button>
						<button type="button" className="sm-short__tool sm-short__tool--end" onClick={() => setEditing(true)}>
							제목 고치기
						</button>
					</div>
				</div>
			</div>

			{editing && (
				<TitleModal
					clip={clip}
					title={title}
					onClose={() => setEditing(false)}
					onSave={(next) =>
						act(async () => {
							await patchClipTitle(clip.id, next);
							setEditing(false);
							reload();
						})
					}
				/>
			)}
			{uploading && (
				<YoutubeUploadModal
					clip={clip}
					onClose={() => setUploading(false)}
					onSubmit={(draft) => {
						// 잡이다 — 진행은 위 배너가, 결과는 카드의 유튜브 줄이 말한다.
						submit(() => uploadClipToYoutube(clip.id, draft));
						setUploading(false);
					}}
				/>
			)}
		</article>
	);
}

/**
 * 제목 고치기. 🔴 시청자 목록에 그대로 보이는 문장이다. 기본값은 질문이나 구간 설명이라 제목으로 쓰라고
 * 쓴 문장이 아니다 — 여기서 다듬는다. 좁은 카드 안에서 고치면 입력칸이 두세 줄로 접혀 읽히지 않아 모달로 띄운다.
 */
function TitleModal({
	clip,
	title,
	onClose,
	onSave,
}: {
	clip: ShortsClip;
	title: string;
	onClose: () => void;
	onSave: (title: string) => void;
}) {
	const [draft, setDraft] = useState(clip.title ?? "");
	return (
		<Modal open title="제목 고치기" onClose={onClose} subhead="시청자 목록에 이 문장이 그대로 보입니다.">
			<form
				className="sm-shorts__titleform"
				onSubmit={(event) => {
					event.preventDefault();
					if (draft.trim()) {
						onSave(draft);
					}
				}}
			>
				{clip.question && <p className="sm-meta">원래 질문 — {clip.question}</p>}
				<input
					className="field__input"
					value={draft}
					autoFocus
					maxLength={200}
					placeholder={title}
					aria-label="제목"
					onChange={(event) => setDraft(event.target.value)}
				/>
				<div className="sm-shorts__titlefoot">
					<button type="button" className="button" onClick={onClose}>
						취소
					</button>
					<button type="submit" className="button sm-go" disabled={draft.trim().length === 0}>
						저장
					</button>
				</div>
			</form>
		</Modal>
	);
}
