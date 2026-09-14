import { useState } from "react";
import { Link } from "react-router-dom";
import { clipUrl, patchClipTitle, publishClip, renderClip, reviewClip } from "../api";
import { ClipVideo } from "./ClipVideo";
import { time } from "../../shared/format";
import type { ShortsClip } from "../types";
import type { SourceView } from "../SourcePage";
import "../clips.css";

// 거르개. "공개" 탭이지만 안에는 아직 안 내보낸 클립도 있다 — 둘을 한 번에 가를 수 있어야
// 탭 이름이 거짓말이 되지 않는다.
const FILTERS: { key: string; label: string; match: (clip: ShortsClip) => boolean }[] = [
	{ key: "all", label: "전체", match: () => true },
	{ key: "live", label: "공개 중", match: (clip) => clip.published_at !== null },
	{ key: "hidden", label: "비공개", match: (clip) => clip.published_at === null },
];

/**
 * 공개 탭 — 만들어진 클립 전부와, 시청자에게 내보내는 자리.
 *
 * 🔴 예전 "클립" 탭은 이 목록과 "직접 골라 만들기"(5~7단계)를 한 화면에 쌓았고, 만들기는 접힌 채
 * 맨 아래에 있었다. 하는 일이 다르다 — 이쪽은 **이미 만든 것을 보고 내보내는** 일이고, 저쪽은
 * **새로 뽑는** 일이다. 그래서 탭을 갈랐다(새 클립 탭, 2026-09-14).
 *
 * 카드는 한 줄에 둘이다. 세로로 하나씩 쌓으니 9:16 영상 옆 오른쪽이 텅 비었다.
 */
export function PublishTab({ view }: { view: SourceView }) {
	const [filter, setFilter] = useState("all");
	const { data } = view;
	if (!data) {
		return null;
	}

	const clips = data.clips;
	const active = FILTERS.find((f) => f.key === filter) ?? FILTERS[0];
	// 순서는 서버가 준 그대로(최신 먼저) — 방금 만든 것이 위에 있어야 한다.
	const visible = clips.filter(active.match);
	const liveCount = clips.filter((clip) => clip.published_at !== null).length;

	return (
		<>
			<div className="sm-shelf__head">
				<h2 className="sm-shelf__title">완성 클립</h2>
				<span className="sm-meta">
					{clips.length}개 · 시청자에게 공개 중 {liveCount}개
				</span>
				{clips.length > 0 && (
					<span className="sm-chips sm-actions--end">
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
			</div>

			{clips.length === 0 && (
				<p className="sm-shelf__empty">
					아직 만든 클립이 없습니다.{" "}
					<Link to={`/sources/${data.source.id}/questions`}>질문</Link> 탭에서 시청자 질문에
					답하거나, <Link to={`/sources/${data.source.id}/make`}>새 클립</Link> 탭에서 기준을 적어
					직접 만드세요.
				</p>
			)}
			{clips.length > 0 && visible.length === 0 && (
				<p className="sm-shelf__empty">여기에 해당하는 클립이 없습니다</p>
			)}

			{/* 🔴 질문(기준)별 묶음 머리글을 카드 **안**으로 넣었다. 묶음마다 머리글을 따로 달면
			    묶음 대부분이 클립 하나라서, 두 칸 격자가 매번 한 칸만 차고 옆이 빈다. */}
			<div className="sm-shelf">
				{visible.map((clip) => (
					<ClipCard key={clip.id} clip={clip} view={view} />
				))}
			</div>
		</>
	);
}

function ClipCard({ clip, view }: { clip: ShortsClip; view: SourceView }) {
	const { data, jobBusy, submit, act, reload, titleEdit, setTitleEdit, titleDraft, setTitleDraft } =
		view;
	const live = clip.published_at !== null;
	const editing = titleEdit === clip.id;
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
		<article className={`sm-card${live ? " sm-card--live" : ""}`}>
			<div className="sm-card__media">
				{clip.rendered ? (
					<ClipVideo src={clipUrl(clip.id)} width="100%" />
				) : (
					<div className="sm-card__pending">
						<span>아직 렌더 전</span>
						<button
							type="button"
							className="button button--small sm-go"
							disabled={jobBusy}
							onClick={() => submit(() => renderClip(clip.id))}
						>
							7. 렌더하기
						</button>
					</div>
				)}
			</div>

			<div className="sm-card__body">
				{/* 🔴 이 클립이 무엇의 답인가. 시청자 질문에서 나온 것과 크리에이터가 자기 기준으로 뽑은
				    것이 한 격자에 섞이므로, "클립 3" 이라는 이름만으로는 알 수 없는 걸 첫 줄에서 가른다. */}
				<div className="sm-card__origin">
					{clip.question ? (
						<>
							<span className="sm-kind">시청자 질문</span>
							{clip.asked_by > 1 && <span>{clip.asked_by}명이 물어봤어요</span>}
						</>
					) : (
						<>
							<span className="sm-kind sm-kind--own">내 기준</span>
							<span className="sm-card__criteria" title={clip.criteria_prompt ?? undefined}>
								{clip.criteria_prompt ?? "기준 없음"}
							</span>
						</>
					)}
					<span className={`sm-live${live ? " sm-live--on" : ""}`}>{live ? "공개 중" : "비공개"}</span>
				</div>

				{/* 🔴 시청자 목록에 그대로 보이는 문장이다. 기본값은 질문이나 구간 설명이라
				    제목으로 쓰라고 쓴 문장이 아니다 — 여기서 다듬는다. */}
				{editing ? (
					<div className="sm-card__titleedit">
						<input
							className="field__input"
							value={titleDraft}
							autoFocus
							maxLength={200}
							aria-label="제목"
							onChange={(event) => setTitleDraft(event.target.value)}
						/>
						<span className="sm-actions">
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
						</span>
					</div>
				) : (
					<div className="sm-card__titlerow">
						<h3 className="sm-card__title">{title}</h3>
						<button
							type="button"
							className="sm-card__edit"
							onClick={() => {
								setTitleEdit(clip.id);
								setTitleDraft(clip.title ?? "");
							}}
						>
							제목 고치기
						</button>
					</div>
				)}

				{/* 제목을 고쳤으면 원래 무엇을 물었는지가 제목에서 사라진다. 같으면 두 번 쓰지 않는다. */}
				{clip.question && clip.question !== title && (
					<p className="sm-card__question">Q. {clip.question}</p>
				)}

				<span className="sm-card__meta">
					{seconds}초 · {time(clip.start_sec)}~{time(clip.end_sec)}
					{stitched && " 에서 이어붙임"}
					{clip.score !== null && ` · ${clip.score}점`} · 클립 {clip.id}
				</span>

				{clip.reason && <p className="sm-card__reason">{clip.reason}</p>}

				<div className="sm-card__foot">
					<div className="sm-actions">
						{/* 🔴 질문에 답한 클립은 질문 패널에서 발행하는 게 주된 길이다(클러스터 상태가 함께
						    움직여야 하므로). 여기 있는 건 크리에이터가 자기 기준으로 뽑은 클립에도 시청자에게
						    보여줄 길이 있어야 해서 둔 것이다. */}
						{clip.rendered && (
							<button
								type="button"
								className={`button button--small${live ? "" : " sm-go"}`}
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
						)}
						{live && data && (
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

					<div className="sm-card__review">
						{/* 누른 쪽이 눌린 채로 보여야 한다 — 안 그러면 들어갔는지 몰라 다시 누른다(연타의 출발점). */}
						<button
							type="button"
							className="button button--small"
							aria-pressed={myVerdict === "OK"}
							onClick={() => act(() => reviewClip(clip, "OK"))}
						>
							쓸만함
						</button>
						<button
							type="button"
							className="button button--small button--danger"
							aria-pressed={myVerdict === "NG"}
							onClick={() => act(() => reviewClip(clip, "NG"))}
						>
							아님
						</button>
						{myVerdict && (
							<span className="sm-meta">내 평가: {myVerdict === "OK" ? "쓸만함" : "아님"}</span>
						)}
					</div>
				</div>
			</div>
		</article>
	);
}
