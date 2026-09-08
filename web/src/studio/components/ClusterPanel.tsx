import { useState } from "react";
import { aggregateQuestions, answerCluster, clipUrl, patchCluster, publishClip } from "../api";
import { ClipVideo } from "./ClipVideo";
import type { ShortsCluster, ShortsClusterClip, ShortsClusterList, ShortsJob } from "../types";
import "../clusters.css";

// 상태 이름은 DB 값 그대로 오므로(question_clusters.status) 화면 문구는 여기서만 정한다.
const STATUS_LABEL: Record<ShortsCluster["status"], string> = {
	OPEN: "대기",
	IN_PROGRESS: "만드는 중",
	REVIEW: "검토 대기",
	PUBLISHED: "발행됨",
	DECLINED: "보류",
	UNANSWERABLE: "답할 구간 없음",
};

// mm:ss. SourcePage 에도 같은 함수가 있지만 가져오면 페이지와 컴포넌트가 서로를 import 한다.
function time(seconds: number): string {
	const total = Math.round(seconds);
	return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

interface ClusterPanelProps {
	sourceId: number;
	/** 비공개면 시청자 화면에 안 보이니 질문이 애초에 들어올 수 없다. 빈 화면의 이유를 이걸로 설명한다. */
	published: boolean;
	busy: boolean;
	/**
	 * 페이지가 대신 읽어 온 질문 목록. 여기서 직접 읽지 않는 이유는 탭 바가 같은 목록으로
	 * "답을 기다리는 질문 수" 배지를 그리기 때문이다 — 두 곳에서 읽으면 요청이 두 번 나가고
	 * 두 숫자가 어긋난다.
	 */
	list: { data: ShortsClusterList | null; error: Error | null };
	/** 페이지의 submit(). 집계와 [답하기] 둘 다 잡이라 진행 배너와 폴링을 그대로 공유한다. */
	onJob: (start: () => Promise<ShortsJob>) => void;
	/** 잡이 아닌 변경(대표 문장·상태·발행) 뒤 목록을 다시 읽게 한다(페이지가 reloadToken 을 올린다). */
	onChanged: () => void;
}

export function ClusterPanel({
	sourceId,
	published,
	busy,
	list,
	onJob,
	onChanged,
}: ClusterPanelProps) {
	const [open, setOpen] = useState<number | null>(null);
	const [editing, setEditing] = useState<number | null>(null);
	const [draft, setDraft] = useState("");
	const [saving, setSaving] = useState(false);
	// 지금 요청이 날아가 있는 클러스터. 발행/보류/다시 열기가 겹쳐 눌리는 걸 막는다.
	const [pending, setPending] = useState<number | null>(null);
	const [error, setError] = useState<string | null>(null);

	const clusters = list.data?.clusters ?? [];
	const unclustered = list.data?.unclustered ?? [];
	const total = clusters.reduce((n, c) => n + c.question_count, 0) + unclustered.length;

	// 🔴 대표 문장은 사람이 고치는 게 유일한 경로다. 재집계는 증분이라 이미 붙은 질문을 건드리지
	// 않고, 서버도 자동 재작성을 하지 않는다(answers/clusters.rename) — 여기서 안 고치면 처음
	// LLM 이 지은 문장이 영원히 남는다. 이 문장이 그대로 run 의 기준 프롬프트가 되므로 중요하다.
	async function save(clusterId: number) {
		setError(null);
		setSaving(true);
		try {
			await patchCluster(clusterId, { canonicalText: draft });
			setEditing(null);
			onChanged();
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setSaving(false);
		}
	}

	// 상태 전이와 발행은 서버가 허용 표로 막는다(clusters.TRANSITIONS). 화면에서 미리 따지지 않고
	// 400 의 한국어 메시지를 그대로 여기 띄운다 — 두 곳에 규칙을 적으면 반드시 어긋난다.
	async function act(clusterId: number, run: () => Promise<unknown>) {
		setError(null);
		setPending(clusterId);
		try {
			await run();
			onChanged();
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setPending(null);
		}
	}

	const aggregateHint =
		unclustered.length === 0
			? "새로 묶을 질문이 없습니다"
			: `안 묶인 질문 ${unclustered.length}개를 묶습니다`;

	return (
		<section className="sm-clusters">
			<div className="sm-clusters__head">
				<h2 className="sm-clusters__title">시청자 질문</h2>
				<span className="sm-meta">
					묶인 클러스터 {clusters.length} · 아직 안 묶인 질문 {unclustered.length}
				</span>
				<span className="sm-actions sm-actions--end">
					<span className="sm-meta">{aggregateHint}</span>
					<button
						type="button"
						className="button button--small"
						disabled={busy || unclustered.length === 0}
						title={busy ? "다른 작업이 끝나야 실행할 수 있습니다" : aggregateHint}
						onClick={() => onJob(() => aggregateQuestions(sourceId))}
					>
						집계
					</button>
				</span>
			</div>

			{list.error && <p className="state state--error">{list.error.message}</p>}
			{error && <p className="state state--error">{error}</p>}

			{list.data && total === 0 && (
				<p className="sm-meta">
					아직 시청자가 남긴 질문이 없습니다.
					{!published && " 이 영상을 시청자에게 공개해야 질문을 받을 수 있습니다."}
				</p>
			)}

			{clusters.map((cluster) => (
				<article className="sm-cluster" key={cluster.id}>
					<div className="sm-cluster__head">
						{editing === cluster.id ? (
							<div className="sm-cluster__editor">
								<textarea
									className="field__input"
									value={draft}
									onChange={(e) => setDraft(e.target.value)}
									aria-label="대표 문장"
								/>
								<span className="sm-actions">
									<button
										type="button"
										className="button button--small sm-go"
										disabled={saving || draft.trim() === ""}
										onClick={() => save(cluster.id)}
									>
										저장
									</button>
									<button
										type="button"
										className="button button--small"
										disabled={saving}
										onClick={() => setEditing(null)}
									>
										취소
									</button>
								</span>
							</div>
						) : (
							<>
								{/* 질문 문장이 첫 줄을 통째로 쓴다. 배지·버튼과 같은 줄에 두면 오른쪽 끝이
								    붐벼서 정작 읽어야 할 문장이 뒤로 밀린다. */}
								<button
									type="button"
									className="sm-cluster__toggle"
									onClick={() => setOpen(open === cluster.id ? null : cluster.id)}
								>
									<span className="sm-cluster__mark">{open === cluster.id ? "▾" : "▸"}</span>
									{cluster.canonical_text}
								</button>
								<div className="sm-cluster__row">
									<span className="sm-meta">
										질문 {cluster.question_count} · 좋아요 {cluster.like_count}
									</span>
									<span className={`sm-status sm-status--${badge(cluster.status)}`}>
										{STATUS_LABEL[cluster.status]}
									</span>
									{/* 상태가 곧 다음 행동이다 — 무엇을 누를 수 있는지 배지 옆에서 바로 끝난다. */}
									{cluster.status === "OPEN" && (
										<button
											type="button"
											className="button button--small sm-go"
											disabled={busy}
											title={busy ? "다른 작업이 끝나야 실행할 수 있습니다" : undefined}
											onClick={() => onJob(() => answerCluster(cluster.id))}
										>
											답하기
										</button>
									)}
									{cluster.status === "IN_PROGRESS" && <span className="sm-meta">만드는 중…</span>}
									{/* 보류(DECLINED)와 답할 구간 없음(UNANSWERABLE)은 둘 다 되돌릴 수 있는 상태다
									    (answers/clusters.TRANSITIONS). 화면에 길을 두지 않으면 한 번 보류한 질문이
									    영원히 묻힌다 — 대표 문장을 고치거나 구간을 더 나눈 뒤 다시 시도할 수 있어야 한다. */}
									{(cluster.status === "UNANSWERABLE" || cluster.status === "DECLINED") && (
										<button
											type="button"
											className="button button--small"
											disabled={pending === cluster.id}
											onClick={() =>
												act(cluster.id, () => patchCluster(cluster.id, { status: "OPEN" }))
											}
										>
											다시 열기
										</button>
									)}
									<button
										type="button"
										className="button button--small"
										onClick={() => {
											setEditing(cluster.id);
											setDraft(cluster.canonical_text);
											setError(null);
										}}
									>
										고치기
									</button>
								</div>
							</>
						)}
					</div>

					{/* 🔴 `답할 구간 없음` 배지만 보면 크리에이터는 아무것도 알 수 없다 — 영상이 정말 그
					    주제를 안 다룬 건지, 검색이 엉뚱한 구간을 본 건지 구분이 안 된다. 실패가 아니라
					    정직한 결과이므로 오류 상자가 아니라 옅은 본문으로 둔다. */}
					{cluster.status === "UNANSWERABLE" && cluster.run_note && (
						<p className="sm-cluster__why">{cluster.run_note}</p>
					)}
					{/* 이쪽은 진짜 실패다. 상태와 무관하게 보여준다 — 실패한 run 은 상태를 못 옮긴다. */}
					{cluster.run_error && (
						<p className="sm-cluster__why sm-cluster__why--error">{cluster.run_error}</p>
					)}

					{/* 발행 전 검토와 발행 뒤 확인이 같은 자리다. 발행된 클립도 계속 보여준다 —
					    "지금 시청자에게 나가 있는 게 뭔가"를 여기 말고 볼 데가 없다. */}
					{cluster.clip && (cluster.status === "REVIEW" || cluster.status === "PUBLISHED") && (
						<ClusterClip
							clip={cluster.clip}
							sourceId={sourceId}
							status={cluster.status}
							busy={pending === cluster.id}
							onPublish={(next, clipId) => act(cluster.id, () => publishClip(clipId, next))}
							onDecline={() =>
								act(cluster.id, () => patchCluster(cluster.id, { status: "DECLINED" }))
							}
						/>
					)}

					{open === cluster.id && (
						<ul className="sm-cluster__questions">
							{cluster.questions.map((q) => (
								<li key={q.id}>
									{q.text} <span className="sm-meta">· 좋아요 {q.likes}</span>
								</li>
							))}
						</ul>
					)}
				</article>
			))}

			{unclustered.length > 0 && (
				<details className="sm-fold" style={{ marginTop: 12 }}>
					<summary>아직 안 묶인 질문 {unclustered.length}개</summary>
					<ul className="sm-cluster__questions" style={{ paddingLeft: 18 }}>
						{unclustered.map((q) => (
							<li key={q.id}>
								{q.text} <span className="sm-meta">· 좋아요 {q.likes}</span>
							</li>
						))}
					</ul>
				</details>
			)}
		</section>
	);
}

interface ClusterClipProps {
	clip: ShortsClusterClip;
	sourceId: number;
	status: ShortsCluster["status"];
	busy: boolean;
	onPublish: (published: boolean, clipId: number) => void;
	onDecline: () => void;
}

/** 만들어진 숏폼 하나. 크리에이터가 발행 여부를 정하는 데 필요한 것만 놓는다. */
function ClusterClip({ clip, sourceId, status, busy, onPublish, onDecline }: ClusterClipProps) {
	// 🔴 judge 가 NG 를 준 클립을 그대로 발행하는 것이 이 화면이 막으려는 사고다. 그래서 소견을
	// 글자 하나로 흘리지 않고 블록 테두리까지 바꾼다 — 발행 버튼이 바로 옆에 있다.
	const ng = clip.llmVerdict === "NG";
	return (
		<div className={`sm-cluster__clip${ng ? " sm-cluster__clip--ng" : ""}`}>
			{clip.rendered ? (
				<ClipVideo src={clipUrl(clip.id)} width={180} />
			) : (
				<p className="sm-meta">아직 렌더되지 않아 재생할 수 없습니다</p>
			)}
			<div className="sm-cluster__clipbody">
				<div className="sm-actions">
					{clip.total_sec !== null && (
						<span className="sm-badge">{Math.round(clip.total_sec)}초</span>
					)}
					{/* 흩어진 구간을 이어붙였다는 사실 자체가 이 제품이 파는 것이다. 접어두지 않는다. */}
					{clip.parts.length > 1 && (
						<span className="sm-badge">{clip.parts.length}조각을 이어붙임</span>
					)}
					{clip.score !== null && <span className="sm-score">{clip.score}점</span>}
					{clip.llmVerdict !== null && (
						<span className={`sm-verdict${ng ? " sm-verdict--ng" : ""}`}>
							판정 {clip.llmVerdict}
						</span>
					)}
				</div>

				{clip.parts.length > 1 && (
					<ol className="sm-cluster__parts">
						{clip.parts.map((part) => (
							<li key={part.ordinal}>
								{time(part.start_sec)} ~ {time(part.end_sec)}{" "}
								<span className="sm-meta">{Math.round(part.end_sec - part.start_sec)}초</span>
							</li>
						))}
					</ol>
				)}

				{clip.llmNote && (
					<p className={`sm-cluster__note${ng ? " sm-cluster__note--ng" : ""}`}>{clip.llmNote}</p>
				)}
				{clip.reason && <p className="sm-meta">{clip.reason}</p>}

				<div className="sm-actions">
					{status === "REVIEW" ? (
						<>
							{/* NG 에는 강조를 빼서 손이 먼저 가지 않게 한다. 막지는 않는다 — 판정은 LLM 이고
							    최종 판단은 사람이다. */}
							<button
								type="button"
								className={`button button--small${ng ? "" : " sm-go"}`}
								disabled={busy || !clip.rendered}
								onClick={() => onPublish(true, clip.id)}
							>
								발행
							</button>
							<button
								type="button"
								className="button button--small"
								disabled={busy}
								onClick={onDecline}
							>
								보류
							</button>
						</>
					) : (
						<>
							<span className="sm-meta">시청자에게 발행됨</span>
							<button
								type="button"
								className="button button--small button--danger"
								disabled={busy}
								onClick={() => onPublish(false, clip.id)}
							>
								내리기
							</button>
							<a
								className="button button--small"
								href={`/watch/${sourceId}`}
								target="_blank"
								rel="noreferrer"
							>
								시청자 화면 열기
							</a>
						</>
					)}
				</div>
			</div>
		</div>
	);
}

function badge(status: ShortsCluster["status"]): string {
	return status.toLowerCase().replace("_", "-");
}
