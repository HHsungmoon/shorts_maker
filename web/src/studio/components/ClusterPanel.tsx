import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
	aggregateQuestions,
	answerCluster,
	buildCandidate,
	clipUrl,
	patchCluster,
	publishClip,
} from "../api";
import { useAuth } from "../AuthContext";
import { ClipVideo } from "./ClipVideo";
import { time } from "../../shared/format";
import type {
	ShortsCandidate,
	ShortsCluster,
	ShortsClusterClip,
	ShortsClusterList,
	ShortsJob,
} from "../types";
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

/**
 * 목록 위 거르개. 상태 여섯 개를 그대로 칩으로 늘어놓으면 고르는 일 자체가 일이 된다 —
 * 크리에이터가 실제로 묻는 건 "지금 내가 손댈 게 뭐냐 / 나간 게 뭐냐 / 접어 둔 게 뭐냐" 셋이다.
 */
const FILTERS: { key: string; label: string; statuses: ShortsCluster["status"][] | null }[] = [
	{ key: "all", label: "전체", statuses: null },
	{ key: "todo", label: "할 일", statuses: ["OPEN", "IN_PROGRESS", "REVIEW"] },
	{ key: "published", label: "발행됨", statuses: ["PUBLISHED"] },
	{ key: "closed", label: "보류·답 없음", statuses: ["DECLINED", "UNANSWERABLE"] },
];

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

/**
 * 시청자 질문 — **왼쪽 목록 · 오른쪽 상세.**
 *
 * 🔴 예전에는 질문마다 후보 3개와 클립을 통째로 펼쳐 세로로 쌓았다. 질문 하나가 화면 몇 개
 * 길이가 되어서 "질문이 몇 개고 각각 어디까지 왔나" 를 보려면 끝까지 스크롤해야 했다
 * (2026-09-14 사용자 피드백). 목록은 **상태를 훑는 자리**, 상세는 **한 질문을 끝내는 자리**로 가른다.
 */
export function ClusterPanel({
	sourceId,
	published,
	busy,
	list,
	onJob,
	onChanged,
}: ClusterPanelProps) {
	const [editing, setEditing] = useState<number | null>(null);
	const [draft, setDraft] = useState("");
	const [saving, setSaving] = useState(false);
	// 지금 요청이 날아가 있는 클러스터. 발행/보류/다시 열기가 겹쳐 눌리는 걸 막는다.
	const [pending, setPending] = useState<number | null>(null);
	const [error, setError] = useState<string | null>(null);
	const { canAct, refuse } = useAuth();
	const [filter, setFilter] = useState("all");
	// 🔴 고른 질문은 URL 에 둔다(`?cluster=12`). 로컬 state 로 두면 새로고침하면 첫 질문으로
	// 돌아가고, 링크로 "이 질문 좀 봐 줘" 를 건넬 수도 없다. replace 로 바꿔서 목록을 훑는 동안
	// 뒤로가기 기록이 쌓이지 않게 한다.
	const [params, setParams] = useSearchParams();
	const detailRef = useRef<HTMLDivElement>(null);

	const clusters = list.data?.clusters ?? [];
	const unclustered = list.data?.unclustered ?? [];
	const total = clusters.reduce((n, c) => n + c.question_count, 0) + unclustered.length;

	const activeFilter = FILTERS.find((f) => f.key === filter) ?? FILTERS[0];
	const inFilter = (c: ShortsCluster, f = activeFilter) =>
		f.statuses === null || f.statuses.includes(c.status);
	const visible = clusters.filter((c) => inFilter(c));
	const wanted = Number(params.get("cluster"));
	// 고른 게 거르개에 가려지면 보이는 첫 줄로 넘어간다 — 목록에 표시가 없는 질문을 오른쪽에
	// 띄워 두면 "지금 뭘 보고 있나" 가 목록과 어긋난다.
	const current = visible.find((c) => c.id === wanted) ?? visible[0] ?? null;

	function select(clusterId: number) {
		// 다른 질문으로 옮기면 쓰던 편집과 앞 질문의 오류는 버린다 — 남겨 두면 엉뚱한 질문에 붙어 보인다.
		setEditing(null);
		setError(null);
		setParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.set("cluster", String(clusterId));
				return next;
			},
			{ replace: true },
		);
		// 좁은 화면에서는 상세가 목록 **아래**로 내려간다. 눌러도 아무 일 없어 보이지 않게 데려간다.
		if (window.matchMedia("(max-width: 860px)").matches) {
			requestAnimationFrame(() =>
				detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
			);
		}
	}

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
		// 🔴 이 패널은 자기 실행 함수를 따로 들고 있다(useStudioJob 의 act 가 아니다). 여기도 막는다.
		if (!canAct) {
			refuse();
			return;
		}
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

			{list.data && total === 0 && (
				<p className="sm-qempty">
					아직 시청자가 남긴 질문이 없습니다.
					{!published && " 이 영상을 시청자에게 공개해야 질문을 받을 수 있습니다."}
				</p>
			)}

			{(clusters.length > 0 || unclustered.length > 0) && (
				<div className="sm-qsplit">
					{/* 왼쪽 — 질문 목록 ---------------------------------------------------- */}
					<nav className="sm-qlist" aria-label="질문 목록">
						<div className="sm-qfilter" role="tablist">
							{FILTERS.map((f) => {
								const count = clusters.filter((c) => inFilter(c, f)).length;
								return (
									<button
										key={f.key}
										type="button"
										role="tab"
										aria-selected={filter === f.key}
										className={`sm-chip${filter === f.key ? " sm-chip--on" : ""}`}
										onClick={() => setFilter(f.key)}
									>
										{f.label} <span className="sm-chip__n">{count}</span>
									</button>
								);
							})}
						</div>

						{visible.length === 0 && clusters.length > 0 && (
							<p className="sm-qlist__empty">여기에 해당하는 질문이 없습니다</p>
						)}

						<ul className="sm-qlist__items">
							{visible.map((cluster) => {
								const on = current?.id === cluster.id;
								return (
									<li key={cluster.id}>
										<button
											type="button"
											className={`sm-qitem${on ? " sm-qitem--on" : ""}`}
											aria-current={on ? "true" : undefined}
											onClick={() => select(cluster.id)}
										>
											<span className="sm-qitem__text">{cluster.canonical_text}</span>
											<span className="sm-qitem__meta">
												<span className={`sm-status sm-status--${badge(cluster.status)}`}>
													{STATUS_LABEL[cluster.status]}
												</span>
												<span>
													질문 {cluster.question_count} · 좋아요 {cluster.like_count}
												</span>
											</span>
										</button>
									</li>
								);
							})}
						</ul>

						{unclustered.length > 0 && (
							<details className="sm-qlist__loose">
								<summary>아직 안 묶인 질문 {unclustered.length}개</summary>
								<ul className="sm-cluster__questions">
									{unclustered.map((q) => (
										<li key={q.id}>
											{q.text} <span className="sm-meta">· 좋아요 {q.likes}</span>
										</li>
									))}
								</ul>
								<p className="sm-meta">[집계] 를 누르면 비슷한 질문끼리 묶여 목록에 올라옵니다.</p>
							</details>
						)}
					</nav>

					{/* 오른쪽 — 고른 질문 하나 ------------------------------------------------ */}
					<div className="sm-qdetail" ref={detailRef}>
						{error && <p className="sm-qdetail__error">{error}</p>}
						{current ? (
							<ClusterDetail
								key={current.id}
								cluster={current}
								sourceId={sourceId}
								busy={busy}
								pending={pending === current.id}
								editing={editing === current.id}
								draft={draft}
								saving={saving}
								onDraft={setDraft}
								onEdit={() => {
									setEditing(current.id);
									setDraft(current.canonical_text);
									setError(null);
								}}
								onCancelEdit={() => setEditing(null)}
								onSave={() => save(current.id)}
								onJob={onJob}
								onAct={(run) => act(current.id, run)}
							/>
						) : (
							<p className="sm-qdetail__placeholder">
								{clusters.length === 0
									? "아직 묶인 질문이 없습니다. [집계] 를 누르면 목록이 생깁니다."
									: "왼쪽에서 질문을 고르세요."}
							</p>
						)}
					</div>
				</div>
			)}
		</section>
	);
}

interface ClusterDetailProps {
	cluster: ShortsCluster;
	sourceId: number;
	busy: boolean;
	pending: boolean;
	editing: boolean;
	draft: string;
	saving: boolean;
	onDraft: (text: string) => void;
	onEdit: () => void;
	onCancelEdit: () => void;
	onSave: () => void;
	onJob: (start: () => Promise<ShortsJob>) => void;
	onAct: (run: () => Promise<unknown>) => void;
}

/** 질문 하나를 끝내는 자리. 위에서 아래로 "무엇을 묻나 → 지금 어디까지 → 결과 → 다른 선택지" 순이다. */
function ClusterDetail({
	cluster,
	sourceId,
	busy,
	pending,
	editing,
	draft,
	saving,
	onDraft,
	onEdit,
	onCancelEdit,
	onSave,
	onJob,
	onAct,
}: ClusterDetailProps) {
	const showClip =
		cluster.clip !== null && (cluster.status === "REVIEW" || cluster.status === "PUBLISHED");
	return (
		<article className="sm-qd">
			<header className="sm-qd__head">
				<div className="sm-qd__top">
					<span className={`sm-status sm-status--${badge(cluster.status)}`}>
						{STATUS_LABEL[cluster.status]}
					</span>
					<span className="sm-meta">
						질문 {cluster.question_count} · 좋아요 {cluster.like_count}
					</span>
				</div>

				{editing ? (
					<div className="sm-cluster__editor">
						<textarea
							className="field__input"
							value={draft}
							onChange={(e) => onDraft(e.target.value)}
							aria-label="대표 문장"
						/>
						<span className="sm-actions">
							<button
								type="button"
								className="button button--small sm-go"
								disabled={saving || draft.trim() === ""}
								onClick={onSave}
							>
								저장
							</button>
							<button
								type="button"
								className="button button--small"
								disabled={saving}
								onClick={onCancelEdit}
							>
								취소
							</button>
						</span>
					</div>
				) : (
					<h3 className="sm-qd__title">{cluster.canonical_text}</h3>
				)}

				{!editing && (
					<div className="sm-actions">
						{/* 상태가 곧 다음 행동이다 — 무엇을 누를 수 있는지 제목 바로 아래에서 끝난다. */}
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
								disabled={pending}
								onClick={() => onAct(() => patchCluster(cluster.id, { status: "OPEN" }))}
							>
								다시 열기
							</button>
						)}
						<button type="button" className="button button--small" onClick={onEdit}>
							대표 문장 고치기
						</button>
					</div>
				)}
			</header>

			{/* 🔴 `답할 구간 없음` 배지만 보면 크리에이터는 아무것도 알 수 없다 — 영상이 정말 그
			    주제를 안 다룬 건지, 검색이 엉뚱한 구간을 본 건지 구분이 안 된다. 실패가 아니라
			    정직한 결과이므로 오류 상자가 아니라 옅은 본문으로 둔다. */}
			{cluster.status === "UNANSWERABLE" && cluster.run_note && (
				<p className="sm-qd__why">{cluster.run_note}</p>
			)}
			{/* 이쪽은 진짜 실패다. 상태와 무관하게 보여준다 — 실패한 run 은 상태를 못 옮긴다. */}
			{cluster.run_error && (
				<p className="sm-qd__why sm-qd__why--error">{cluster.run_error}</p>
			)}

			{/* 시청자가 실제로 쓴 문장. 대표 문장은 LLM 이 지은 것이라, 고치기 전에 원문을 볼 수 있어야 한다.
			    묶인 게 하나뿐이면 대표 문장과 거의 같아서 접어 둔다. */}
			{cluster.questions.length > 0 && (
				<details className="sm-qd__asked" open={cluster.questions.length > 1}>
					<summary>시청자가 쓴 질문 {cluster.questions.length}개</summary>
					<ul className="sm-cluster__questions">
						{cluster.questions.map((q) => (
							<li key={q.id}>
								{q.text} <span className="sm-meta">· 좋아요 {q.likes}</span>
							</li>
						))}
					</ul>
				</details>
			)}

			{/* 발행 전 검토와 발행 뒤 확인이 같은 자리다. 발행된 클립도 계속 보여준다 —
			    "지금 시청자에게 나가 있는 게 뭔가"를 여기 말고 볼 데가 없다.
			    🔴 만든 클립이 후보보다 **위**다. 한 질문씩 보는 화면이 되면서 후보 셋(대사 전문)을 다
			    지나야 [발행] 이 나오던 것을 뒤집었다. 아직 안 만들었으면 후보가 곧 첫 내용이다. */}
			{showClip && cluster.clip && (
				<section className="sm-qd__section">
					<h4 className="sm-qd__label">
						{cluster.status === "PUBLISHED" ? "시청자에게 나간 숏폼" : "만든 숏폼"}
					</h4>
					<ClusterClip
						clip={cluster.clip}
						sourceId={sourceId}
						status={cluster.status}
						busy={pending}
						onPublish={(next, clipId) => onAct(() => publishClip(clipId, next))}
						onDecline={() => onAct(() => patchCluster(cluster.id, { status: "DECLINED" }))}
					/>
				</section>
			)}

			{/* 후보는 만든 뒤에도 남겨 둔다 — 다른 후보로 바꿔 볼 수 있어야 한다. */}
			{cluster.status === "REVIEW" && (
				<CandidatePicker
					candidates={cluster.candidates}
					hasClip={showClip}
					busy={pending}
					onBuild={(candidateId) => onAct(() => buildCandidate(candidateId))}
				/>
			)}

			{cluster.status === "OPEN" && cluster.candidates.length === 0 && (
				<p className="sm-qd__placeholder">
					아직 답을 만들지 않았습니다. [답하기] 를 누르면 영상에서 답이 될 구간을 찾아 후보를 만듭니다.
				</p>
			)}
		</article>
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
				<ClipVideo src={clipUrl(clip.id)} width={160} />
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
				{clip.reason && <p className="sm-cluster__reason">{clip.reason}</p>}

				<div className="sm-actions sm-cluster__clipactions">
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


/**
 * 겨룬 후보들 — **대사를 읽고 고르는 자리**.
 *
 * 🔴 **이 화면이 이 제품의 사람 관문이다.** 예전에는 시스템이 승자를 골라 렌더까지 해 버렸다.
 * 크리에이터에게는 클립 하나가 그냥 나온 것으로 보였고, 무엇과 겨뤘는지 알 수 없었다.
 * 판정 결과만 표로 보여주는 것도 답이 아니었다 — "조합, 2조각, 28초, 자립 X, 45점" 만 보고는
 * 고를 수가 없다. **사람은 내용을 읽어야 판단한다.**
 *
 * 판정은 남아 있지만 **추천**일 뿐이다. 점수가 낮아도 읽어 보고 쓸 만하다고 판단할 수 있다.
 */
function CandidatePicker({
	candidates,
	hasClip,
	busy,
	onBuild,
}: {
	candidates: ShortsCandidate[];
	/** 이미 하나로 만들었는가. 그러면 이 블록은 "처음 고르기" 가 아니라 "다른 걸로 바꿔 보기" 다. */
	hasClip: boolean;
	busy: boolean;
	onBuild: (candidateId: number) => void;
}) {
	if (candidates.length === 0) {
		return null;
	}
	return (
		<section className="sm-qd__section sm-cands">
			<h4 className="sm-qd__label">
				{hasClip ? `다른 후보 ${candidates.length}개` : `후보 ${candidates.length}개 — 하나를 고르세요`}
			</h4>
			<p className="sm-cands__lead">
				답이 될 만한 방식 {candidates.length}가지를 만들고 판정까지 마쳤습니다. 대사를 읽어 보고
				하나를 고르세요. 고르는 데는 비용이 들지 않습니다.
			</p>
			{candidates.map((candidate) => (
				<CandidateCard key={candidate.id} candidate={candidate} busy={busy} onBuild={onBuild} />
			))}
		</section>
	);
}

function CandidateCard({
	candidate,
	busy,
	onBuild,
}: {
	candidate: ShortsCandidate;
	busy: boolean;
	onBuild: (candidateId: number) => void;
}) {
	// 🔴 통과 여부가 점수보다 중요하다. 자립하지 않는 클립은 점수가 높아도 시청자가 못 알아본다.
	const passed = candidate.standalone === true && candidate.answers === true;
	return (
		<article className={`sm-cand${candidate.chosen ? " sm-cand--chosen" : ""}`}>
			<header className="sm-cand__head">
				<span className="sm-cand__label">
					{CANDIDATE_LABEL[candidate.label ?? ""] ?? candidate.label ?? "후보"}
				</span>
				<span className="sm-meta">
					{Math.round(candidate.totalSec)}초
					{candidate.parts.length > 1 && ` · ${candidate.parts.length}조각을 이어붙임`}
				</span>
				{candidate.recommended && <span className="sm-cand__flag">추천</span>}
				{candidate.chosen && <span className="sm-cand__flag sm-cand__flag--chosen">선택함</span>}
				{/* 떨어진 관문만 표시한다. 통과한 것에 O 를 잔뜩 붙이면 실패가 눈에 안 띈다. */}
				{candidate.standalone === false && <span className="tag tag--rejected">앞뒤 맥락 필요</span>}
				{candidate.answers === false && <span className="tag tag--rejected">답이 아님</span>}
				{candidate.score !== null && <span className="sm-cand__score">{candidate.score}점</span>}
			</header>

			{/* 🔴 **대사 전문.** 이게 이 카드의 본체다. 조각이 여럿이면 사이에 표시를 넣는다 —
			    실제 영상에서도 그 자리에 "몇 분에서 이어집니다" 안내가 뜬다. */}
			<div className="sm-cand__body">
				{candidate.parts.map((part, index) => (
					<div key={part.ordinal}>
						{index > 0 && (
							<p className="sm-cand__bridge">
								↓ {time(part.start_sec)} 로 건너뜁니다 (영상에도 안내가 뜹니다)
							</p>
						)}
						<p className="sm-cand__text">{part.text}</p>
					</div>
				))}
			</div>

			{candidate.judgeNote && <p className="sm-cand__note">{candidate.judgeNote}</p>}

			<div className="sm-actions">
				<button
					type="button"
					className={`button button--small${passed ? " sm-go" : ""}`}
					disabled={busy}
					onClick={() => onBuild(candidate.id)}
				>
					{candidate.chosen ? "다시 만들기" : "이걸로 만들기"}
				</button>
			</div>
		</article>
	);
}

// 후보의 뜻. 라벨만 보여주면 single 과 tight 의 차이를 알 수 없다.
const CANDIDATE_LABEL: Record<string, string> = {
	single: "단일 컷",
	combo: "조합",
	tight: "짧은 컷",
};
