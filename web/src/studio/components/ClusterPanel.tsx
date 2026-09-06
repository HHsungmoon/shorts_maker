import { useState } from "react";
import { aggregateQuestions, fetchClusters, patchCluster } from "../api";
import { useAsync } from "../../shared/useAsync";
import type { ShortsCluster, ShortsClusterList, ShortsJob } from "../types";
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

interface ClusterPanelProps {
	sourceId: number;
	/** 비공개면 시청자 화면에 안 보이니 질문이 애초에 들어올 수 없다. 빈 화면의 이유를 이걸로 설명한다. */
	published: boolean;
	busy: boolean;
	/** 페이지의 reloadToken. 잡이 끝나면 올라가고, 그때 이 패널도 다시 읽는다. */
	reloadToken: number;
	/** 페이지의 submit(). 잡 진행 배너와 폴링을 공유하려고 그대로 받는다. */
	onAggregate: (start: () => Promise<ShortsJob>) => void;
	/** 대표 문장을 고친 뒤 목록을 다시 읽게 한다(페이지가 reloadToken 을 올린다). */
	onChanged: () => void;
}

export function ClusterPanel({
	sourceId,
	published,
	busy,
	reloadToken,
	onAggregate,
	onChanged,
}: ClusterPanelProps) {
	const [open, setOpen] = useState<number | null>(null);
	const [editing, setEditing] = useState<number | null>(null);
	const [draft, setDraft] = useState("");
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const list = useAsync<ShortsClusterList>(() => fetchClusters(sourceId), [sourceId, reloadToken]);
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
						onClick={() => onAggregate(() => aggregateQuestions(sourceId))}
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
								<button
									type="button"
									className="sm-cluster__toggle"
									onClick={() => setOpen(open === cluster.id ? null : cluster.id)}
								>
									<span className="sm-cluster__mark">{open === cluster.id ? "▾" : "▸"}</span>
									{cluster.canonical_text}
								</button>
								<span className="sm-meta">
									질문 {cluster.question_count} · 좋아요 {cluster.like_count}
								</span>
								<span className={`sm-status sm-status--${badge(cluster.status)}`}>
									{STATUS_LABEL[cluster.status]}
								</span>
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
							</>
						)}
					</div>

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

function badge(status: ShortsCluster["status"]): string {
	return status.toLowerCase().replace("_", "-");
}
