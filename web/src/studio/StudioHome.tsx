import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { deleteMedia, fetchMedia, fetchShortsSources, fetchStatus, fetchYoutube, thumbnailUrl } from "./api";
import { useAuth } from "./AuthContext";
import { MediaLibrary } from "./components/MediaLibrary";
import { NewSourceModal } from "./components/NewSourceModal";
import { StudioBanners, StudioBrand } from "./components/StudioChrome";
import { YoutubeConnect } from "./components/YoutubeConnect";
import { useStudioJob } from "./useStudioJob";
import { useGuide } from "./guide";
import { HOME_GUIDE } from "./guides/home";
import { guideAttr } from "./guides/targets";
import { time } from "../shared/format";
import { useAsync } from "../shared/useAsync";
import type { ShortsMediaList, ShortsSourceListItem, ShortsStatus, YoutubeState } from "./types";
import "./home.css";

/**
 * 이 영상이 **어디까지 왔나** 한 줄.
 *
 * 카드에 숫자 일곱 개를 늘어놓아 봐야 읽는 사람이 다시 판단해야 한다. 파이프라인은 순서가
 * 정해져 있으니(구간 → 전사 → 분할) 막힌 첫 지점 하나만 말하는 편이 짧고 정확하다.
 */
function stage(source: ShortsSourceListItem): { text: string; tone: StageTone } {
	// 등록 자체가 아직 안 끝났다 — 유튜브에서 받는 중이거나 실패다(sources.status).
	if (source.status === "FAILED") {
		return { text: "등록 실패", tone: "failed" };
	}
	if (source.status !== "DONE") {
		return { text: "등록 중…", tone: "pending" };
	}
	if (source.chunk_count === 0) {
		return { text: "준비 안 됨", tone: "todo" };
	}
	if (source.utterance_count === 0) {
		return { text: "전사 필요", tone: "todo" };
	}
	if (source.segment_count === 0) {
		return { text: "주제 분할 필요", tone: "todo" };
	}
	return { text: "준비됨", tone: "ready" };
}

/**
 * 진행 표시의 색. 영상 화면 탭 줄의 "준비 필요" 와 같은 규칙이다 — 아직 안 한 일(todo)은 고장이 아니라
 * 알림 색이고, 빨강은 정말 실패한 것(failed)에만 쓴다.
 */
type StageTone = "failed" | "pending" | "todo" | "ready";

/** 카드 아래 숫자 칸 하나. 0 은 흐리게 둔다 — 다섯 장이 다 "0 0 0" 이면 0 이 아닌 칸이 안 보인다. */
function Stat({ label, value, extra, open = false }: { label: string; value: number; extra?: string; open?: boolean }) {
	const tone = open && value > 0 ? " sm-home-stat--open" : value === 0 ? " sm-home-stat--zero" : "";
	return (
		<div className={`sm-home-stat${tone}`}>
			<dt>{label}</dt>
			<dd>
				{value}
				{extra && <small>{extra}</small>}
			</dd>
		</div>
	);
}

/** `guide` 인 카드 하나만 가이드가 짚는다(HOME_GUIDE 의 영상 카드). */
function Card({ source, guide }: { source: ShortsSourceListItem; guide: boolean }) {
	const { text, tone } = stage(source);
	return (
		<Link
			to={`/studio/sources/${source.id}`}
			className="sm-home-card"
			{...(guide ? guideAttr("source-card") : {})}
		>
			<div className="sm-home-card__thumb">
				{source.youtube_id ? (
					// 로딩에 실패해도 레이아웃이 무너지지 않게 배경색을 깔아 둔다.
					<img src={thumbnailUrl(source.youtube_id)} alt="" loading="lazy" />
				) : (
					<span className="sm-home-card__noimage">미리보기 없음</span>
				)}
				{/* 🔴 시청자 화면(/watch)에 안 보이는 영상만 표시한다. 모든 카드에 "공개 중" 을 달면 아무것도
				    말하지 않는다 — 예외만 눈에 띄면 된다. */}
				{!source.published && <span className="sm-home-card__flag">비공개</span>}
				{/* 유튜브 플레이어와 같은 표기(1:35:00). 원본이 1~2시간짜리 강연이라 "96분" 보다 읽기 쉽다. */}
				{source.duration_sec ? <span className="sm-home-card__duration">{time(source.duration_sec)}</span> : null}
			</div>
			<div className="sm-home-card__body">
				<span className={`sm-home-card__stage sm-home-card__stage--${tone}`}>{text}</span>
				<h2 className="sm-home-card__title" title={source.title}>
					{source.title}
				</h2>
				{source.channel && <p className="sm-home-card__channel">{source.channel}</p>}
			</div>
			<dl className="sm-home-card__stats">
				<Stat label="질문" value={source.question_count} />
				{/* 🔴 답을 기다리는 질문. 이 숫자가 이 영상을 여는 이유이자 제품의 전부다(tease §3) —
				    0 이 아니면 카드에서 혼자 튀어나와야 한다. */}
				<Stat label="답할 것" value={source.open_cluster_count} open />
				<Stat
					label="숏폼"
					value={source.clip_count}
					extra={source.clip_count > 0 ? `공개 ${source.published_clip_count}` : undefined}
				/>
			</dl>
		</Link>
	);
}

export function StudioHome() {
	const { canAct, refuse } = useAuth();
	const studio = useStudioJob();
	const [modalOpen, setModalOpen] = useState(false);
	const openGuide = useGuide(HOME_GUIDE);

	// 목록은 늘 같은 대상(전체)이라 subject 가 없다 — 언제나 유지해도 틀린 내용이 될 일이 없다.
	const status = useAsync<ShortsStatus>(fetchStatus, [studio.reloadToken], { keepPrevious: true });
	const sources = useAsync<ShortsSourceListItem[]>(
		fetchShortsSources, [studio.reloadToken], { keepPrevious: true },
	);
	// 파일 목록은 소스 목록과 다르다 — 등록되지 않은 파일(scp 로 올린 것)이 여기에만 보인다.
	const media = useAsync<ShortsMediaList>(fetchMedia, [studio.reloadToken], { keepPrevious: true });
	const youtube = useAsync<YoutubeState>(fetchYoutube, [studio.reloadToken], { keepPrevious: true });

	const { setError, setNotice, reload } = studio;

	// 유튜브 연결에서 돌아온 자리(서버 콜백이 `/studio?youtube=…&reason=…` 로 보낸다). 안내를 띄우고
	// 주소에서 지운다 — 남겨 두면 새로고침할 때마다 같은 안내가 다시 뜬다.
	const [params, setParams] = useSearchParams();
	useEffect(() => {
		const result = params.get("youtube");
		if (!result) {
			return;
		}
		const reason = params.get("reason");
		if (result === "connected") {
			setNotice("유튜브 채널을 연결했습니다. 영상 페이지의 공개 탭에서 숏폼마다 [유튜브에 올리기] 로 올립니다.");
		} else if (result === "denied") {
			setNotice("유튜브 연결을 취소했습니다.");
		} else {
			setError(`유튜브 연결에 실패했습니다. ${reason ?? ""}`.trim());
		}
		setParams({}, { replace: true });
	}, [params, setParams, setNotice, setError]);
	// 🔴 서버에서 파일과 파생물을 실제로 지운다. 되돌릴 수 없어 무엇이 사라지는지 먼저 말한다.
	const remove = useCallback(
		async (name: string) => {
			// 🔴 삭제는 되돌릴 수 없다. useStudioJob 의 act 를 안 거치는 자리라 여기서 따로 막는다.
			if (!canAct) {
				refuse();
				return;
			}
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
		[canAct, refuse, media.data, setError, setNotice, reload],
	);

	const items = sources.data ?? [];
	// 제목 옆 한 줄 — 영상 전체를 합친 숫자. 어느 영상을 열지는 카드가 말하고, 이 줄은 "할 일이 얼마나
	// 남았나" 만 말한다.
	const totalQuestions = items.reduce((n, s) => n + s.question_count, 0);
	const totalOpen = items.reduce((n, s) => n + s.open_cluster_count, 0);
	const totalClips = items.reduce((n, s) => n + s.clip_count, 0);
	// 가이드가 짚을 카드 — 답할 질문이 가장 많은 영상. 첫 카드를 짚었더니 숫자가 다 0 이라 "색칠된 숫자" 를
	// 화면에서 보여 줄 수 없었다(2026-11-15). 다 0 이면 첫 카드(최신)다.
	const guideId = items.reduce<ShortsSourceListItem | null>(
		(best, s) => (best === null || s.open_cluster_count > best.open_cluster_count ? s : best),
		null,
	)?.id;

	return (
		<div className="sm-shell">
			{/* 🔴 영상 화면과 같은 상단바다(2026-11-15). 홈만 옛 머리(가운데 1100px · 글자 제목)로 남아 있어서
			    영상을 열고 닫을 때마다 다른 서비스를 오가는 것처럼 보였다. 탭 줄은 없다 — 홈은 화면 하나다. */}
			<header className="sm-bar sm-bar--plain">
				<StudioBrand onGuide={openGuide} />
				<div className="sm-bar__row">
					<h1 className="sm-bar__title">영상</h1>
					{items.length > 0 && (
						<span className="sm-bar__meta">
							{items.length}개 · 질문 {totalQuestions} · 답할 것 {totalOpen} · 숏폼 {totalClips}
						</span>
					)}
					<span className="sm-bar__actions">
						{/* 보기 전용 표시는 늘 보이는 자리에 둔다(영상 화면 탭 줄의 것과 같다). 막힌 버튼을 누르면
						    이유는 토스트가 말한다(DeniedToast). */}
						{!canAct && (
							<span
								className="sm-bar__readonly"
								{...guideAttr("readonly")}
								title="화면은 전부 볼 수 있고 실행·발행·수정만 막혀 있습니다"
							>
								보기 전용
							</span>
						)}
						{/* 관리자 기준은 모든 영상에 걸린다 — 영상 페이지가 아니라 여기서 들어간다. */}
						<Link to="/studio/prompts" className="button button--small">
							프롬프트
						</Link>
						<YoutubeConnect
							state={youtube.data}
							onError={setError}
							onChanged={(message) => {
								setNotice(message);
								reload();
							}}
						/>
						{/* 🔴 보기 전용이라고 비활성화하지 않는다. 눌리지 않는 버튼은 이유를 말할 기회가
						    없어서 "고장난 화면" 으로 읽힌다.

						    🔴 **여는 것까지는 보기 전용도 된다**(2026-09-20). 예전엔 누르는 순간 거절했는데,
						    그러면 구경하러 온 사람은 이 도구가 무엇을 받는지(유튜브 URL? 파일?) 영영 못 본다.
						    막아야 하는 것은 **등록이 실제로 도는 것**이지 폼을 읽는 것이 아니다 —
						    거절은 모달 안의 실행 버튼이 한다. */}
						<button
							type="button"
							className="button button--small sm-go"
							{...guideAttr("new-source")}
							onClick={() => setModalOpen(true)}
						>
							+ 새로 만들기
						</button>
					</span>
				</div>
			</header>

			<main className="sm-shell__main">
				{/* 보기 전용 표시는 상단바가 늘 보여주므로 배너에서는 뺀다(영상 화면과 같다). */}
				<StudioBanners status={status} studio={studio} readonlyNotice={false} />

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
					<div className="sm-home-empty">
						<p className="sm-home-empty__title">아직 등록된 영상이 없습니다</p>
						<p className="sm-meta">유튜브 주소를 넣거나 서버에 올려 둔 파일을 등록하면 여기에 카드로 쌓입니다.</p>
						<button type="button" className="button sm-go" onClick={() => setModalOpen(true)}>
							+ 새로 만들기
						</button>
					</div>
				)}

				{items.length > 0 && (
					<div className="sm-home-grid">
						{/* 서버가 id 내림차순으로 준다(studio.list_sources) — 최신이 앞이다. */}
						{items.map((source) => (
							<Card key={source.id} source={source} guide={source.id === guideId} />
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
			</main>
		</div>
	);
}
