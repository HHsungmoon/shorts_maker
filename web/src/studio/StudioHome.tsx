import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { deleteMedia, fetchMedia, fetchShortsSources, fetchStatus, thumbnailUrl } from "./api";
import { PRODUCT_NAME } from "../shared/brand";
import { MediaLibrary } from "./components/MediaLibrary";
import { NewSourceModal } from "./components/NewSourceModal";
import { StudioBanners, StudioHead } from "./components/StudioChrome";
import { useStudioJob } from "./useStudioJob";
import { useAsync } from "../shared/useAsync";
import type { ShortsMediaList, ShortsSourceListItem, ShortsStatus } from "./types";
import "./home.css";

/**
 * 이 영상이 **어디까지 왔나** 한 줄.
 *
 * 카드에 숫자 일곱 개를 늘어놓아 봐야 읽는 사람이 다시 판단해야 한다. 파이프라인은 순서가
 * 정해져 있으니(구간 → 전사 → 분할) 막힌 첫 지점 하나만 말하는 편이 짧고 정확하다.
 */
function stage(source: ShortsSourceListItem): { text: string; modifier: string } {
	// 등록 자체가 아직 안 끝났다 — 유튜브에서 받는 중이거나 실패다(sources.status).
	if (source.status === "FAILED") {
		return { text: "등록 실패", modifier: " sm-home-card__stage--failed" };
	}
	if (source.status !== "DONE") {
		return { text: "등록 중…", modifier: "" };
	}
	if (source.chunk_count === 0) {
		return { text: "준비 안 됨", modifier: "" };
	}
	if (source.utterance_count === 0) {
		return { text: "전사 필요", modifier: "" };
	}
	if (source.segment_count === 0) {
		return { text: "주제 분할 필요", modifier: "" };
	}
	return { text: "준비됨", modifier: " sm-home-card__stage--ready" };
}

function Card({ source }: { source: ShortsSourceListItem }) {
	const { text, modifier } = stage(source);
	return (
		<Link to={`/sources/${source.id}`} className="sm-home-card">
			<div className="sm-home-card__thumb">
				{source.youtube_id ? (
					// 로딩에 실패해도 레이아웃이 무너지지 않게 배경색을 깔아 둔다.
					<img src={thumbnailUrl(source.youtube_id)} alt="" loading="lazy" />
				) : (
					<span className="sm-home-card__noimage">미리보기 없음</span>
				)}
				{source.duration_sec ? (
					<span className="sm-home-card__duration">
						{Math.round(source.duration_sec / 60)}분
					</span>
				) : null}
			</div>
			<div className="sm-home-card__body">
				<h2 className="sm-home-card__title">{source.title}</h2>
				<p className={`sm-home-card__stage${modifier}`}>{text}</p>
				<p className="sm-home-card__meta">
					<span>질문 {source.question_count}</span>
					{/* 🔴 답을 기다리는 질문. 이 숫자가 이 영상을 여는 이유이자 제품의 전부다(tease §3) —
					    0 이 아니면 회색 메타에서 혼자 튀어나와야 한다. */}
					<span className={source.open_cluster_count > 0 ? "sm-home-card__open" : undefined}>
						답할 것 {source.open_cluster_count}
					</span>
					<span>
						숏폼 {source.clip_count}
						{source.clip_count > 0 && `(공개 ${source.published_clip_count})`}
					</span>
				</p>
			</div>
		</Link>
	);
}

export function StudioHome() {
	const studio = useStudioJob();
	const [modalOpen, setModalOpen] = useState(false);

	// 목록은 늘 같은 대상(전체)이라 subject 가 없다 — 언제나 유지해도 틀린 내용이 될 일이 없다.
	const status = useAsync<ShortsStatus>(fetchStatus, [studio.reloadToken], { keepPrevious: true });
	const sources = useAsync<ShortsSourceListItem[]>(
		fetchShortsSources, [studio.reloadToken], { keepPrevious: true },
	);
	// 파일 목록은 소스 목록과 다르다 — 등록되지 않은 파일(scp 로 올린 것)이 여기에만 보인다.
	const media = useAsync<ShortsMediaList>(fetchMedia, [studio.reloadToken], { keepPrevious: true });

	const { setError, setNotice, reload } = studio;
	// 🔴 서버에서 파일과 파생물을 실제로 지운다. 되돌릴 수 없어 무엇이 사라지는지 먼저 말한다.
	const remove = useCallback(
		async (name: string) => {
			const item = media.data?.items.find((i) => i.name === name);
			const extra = item?.sourceId ? "\n전사·구간·클립도 함께 지워집니다." : "";
			if (!window.confirm(`${name} 을(를) 서버에서 삭제합니다.${extra}\n되돌릴 수 없습니다.`)) {
				return;
			}
			setError(null);
			try {
				const result = await deleteMedia(name);
				const freed = (result.freedBytes / (1 << 20)).toFixed(0);
				setNotice(`${result.removed.length}개 파일 삭제 · ${freed}MB 확보`);
				reload();
			} catch (e: unknown) {
				setError(e instanceof Error ? e.message : String(e));
			}
		},
		[media.data, setError, setNotice, reload],
	);

	const items = sources.data ?? [];

	return (
		<div className="page">
			<StudioHead title={PRODUCT_NAME} count={items.length > 0 ? `영상 ${items.length}개` : undefined}>
				<button
					type="button"
					className="button button--small sm-go"
					onClick={() => setModalOpen(true)}
				>
					+ 새로 만들기
				</button>
			</StudioHead>

			<StudioBanners status={status} studio={studio} />

			{/* 등록도 잡이다 — onStarted 로 페이지의 잡에 태우면 진행 배너가 뜨고, 끝나면
			    reloadToken 이 올라가며 아래 목록에 새 영상이 나타난다. */}
			<NewSourceModal
				open={modalOpen}
				onClose={() => setModalOpen(false)}
				unregistered={(media.data?.items ?? []).filter((i) => i.sourceId === null)}
				languages={status.data?.languages ?? { ko: "한국어", en: "영어" }}
				onStarted={studio.adopt}
			/>

			{sources.loading && <p className="state">불러오는 중…</p>}
			{sources.error && (
				// 목록이 남아 있으면 갱신 실패다 — 통째로 못 읽은 것과 문구를 나눈다.
				<p className={sources.data ? "sm-meta" : "state state--error"}>
					{sources.data
						? `잠시 갱신하지 못했습니다. (${sources.error.message})`
						: `목록을 불러오지 못했습니다. ${sources.error.message}`}
				</p>
			)}
			{sources.data && items.length === 0 && (
				<p className="state">
					아직 등록된 영상이 없습니다. 오른쪽 위 “새로 만들기”로 유튜브 주소를 넣으세요.
				</p>
			)}

			{items.length > 0 && (
				<div className="sm-home-grid">
					{/* 서버가 id 내림차순으로 준다(studio.list_sources) — 최신이 앞이다. */}
					{items.map((source) => (
						<Card key={source.id} source={source} />
					))}
				</div>
			)}

			{/* 디스크에 있는 파일 그대로다. 미등록 파일과 용량이 여기에만 보이고, 삭제도 여기서만
			    한다 — 영상을 고르는 일은 위 카드가 하므로 이건 정비용이라 접어 둔다. */}
			{media.data && (
				<details className="sm-fold sm-home-files">
					<summary>서버에 저장된 파일</summary>
					<MediaLibrary media={media.data} onDelete={remove} busy={studio.jobBusy} />
				</details>
			)}
		</div>
	);
}
