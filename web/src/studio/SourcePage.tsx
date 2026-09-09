import type { Dispatch, SetStateAction } from "react";
import { useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useParams } from "react-router-dom";
import { fetchClusters, fetchShortsSource, fetchStatus } from "./api";
import { ClipsTab } from "./components/ClipsTab";
import { LogTab } from "./components/LogTab";
import { PrepareTab } from "./components/PrepareTab";
import { QuestionsTab } from "./components/QuestionsTab";
import { StudioBanners, StudioHead } from "./components/StudioChrome";
import { useStudioJob } from "./useStudioJob";
import { useAsync } from "../shared/useAsync";
import type {
	ShortsChunk,
	ShortsClip,
	ShortsClusterList,
	ShortsJob,
	ShortsRun,
	ShortsSegment,
	ShortsSourceDetail,
	ShortsStatus,
	ShortsUtterance,
} from "./types";
import "./source.css";

const DEFAULT_CRITERIA = "한 문장으로 인용할 만한 핵심 논지";

type RankedPayload = NonNullable<ShortsRun["ranked"]>;
export type RankedEntry = NonNullable<RankedPayload["ranked"]>[number];
export type ExcludedEntry = NonNullable<RankedPayload["excluded"]>[number];

/**
 * 클립 묶음 하나. 묶음의 제목이 곧 그 클립이 답하는 질문(또는 그때 쓴 기준)이다.
 */
export interface ClipGroup {
	key: string;
	question: string | null;
	criteria: string | null;
	askedBy: number;
	clips: ShortsClip[];
}

/**
 * 탭 넷이 나눠 쓰는 화면 상태 전부. 상태와 파생값은 전부 SourcePage 가 들고 있고 탭은
 * 그리기만 한다.
 *
 * 🔴 값을 탭 안으로 내리지 않는 이유는 대부분이 두 탭 이상에서 쓰이기 때문이다 —
 * `segments` 는 준비·클립 양쪽이 보고, `nextStep` 은 위의 모든 값에서 나온다. 탭마다
 * 따로 계산하면 같은 화면이 서로 다른 진행 상태를 말하게 된다.
 */
export interface SourceView {
	/** URL 이 정하는 작업 대상. 탭이 자기 몫의 API 를 부를 때 쓴다. */
	sourceId: number;
	/** 아직 못 읽었으면 null. 각 탭이 자기 자리에서 걸러 쓴다(1단계는 로딩 중에도 보인다). */
	data: ShortsSourceDetail | null;
	status: { data: ShortsStatus | null; error: Error | null };
	/** 질문 목록. 탭 배지와 질문 패널이 같은 값을 봐야 해서 페이지가 한 번만 읽는다. */
	clusters: { data: ShortsClusterList | null; error: Error | null };
	geminiReady: boolean;
	sourceBusy: boolean;
	jobBusy: boolean;
	submit: (start: () => Promise<ShortsJob>) => void;
	act: (run: () => Promise<unknown>) => void;
	reload: () => void;

	chunks: ShortsChunk[];
	hasChunks: boolean;
	utterances: number;
	sttDone: boolean;
	coveredSec: number;
	duration: number;
	chunkStart: number;
	chunkEnd: number;
	segments: ShortsSegment[];
	latestRun: ShortsRun | null;
	ranked: RankedEntry[];
	excluded: ExcludedEntry[];
	clipBySegment: Map<number, ShortsClip>;
	clipGroups: ClipGroup[];
	nextStep: number;
	/** 1~4단계가 다 끝났는가. 끝나야 질문에 답할 수 있다. */
	prepDone: boolean;

	editingRange: boolean;
	rangeStart: number;
	rangeEnd: number;
	rangeValid: boolean;
	setRange: Dispatch<SetStateAction<{ start: number; end: number } | null>>;
	setRangeOpen: Dispatch<SetStateAction<boolean>>;

	criteria: string;
	setCriteria: Dispatch<SetStateAction<string>>;
	language: string | null;
	setLanguage: Dispatch<SetStateAction<string | null>>;
	openPreview: number | null;
	setOpenPreview: Dispatch<SetStateAction<number | null>>;
	showAllRanked: boolean;
	setShowAllRanked: Dispatch<SetStateAction<boolean>>;
	titleEdit: number | null;
	setTitleEdit: Dispatch<SetStateAction<number | null>>;
	titleDraft: string;
	setTitleDraft: Dispatch<SetStateAction<string>>;
	transcript: ShortsUtterance[] | null;
	setTranscript: Dispatch<SetStateAction<ShortsUtterance[] | null>>;
}

const tabClass = ({ isActive }: { isActive: boolean }) => `sm-tab${isActive ? " sm-tab--on" : ""}`;

/**
 * 영상 하나의 작업 화면.
 *
 * 무엇을 작업 중인지는 **URL 이** 정한다(`/sources/:sourceId`). 예전엔 목록과 한 화면이라
 * 로컬 state 로 골랐는데, 그러면 지금 보는 영상을 링크로 줄 수도 북마크할 수도 없었다.
 *
 * 어느 **탭**을 보는지도 마찬가지로 URL 이다(`/sources/3/questions`). 여섯 덩어리를 세로로
 * 쌓아두니 매일 쓰는 질문 루프가 영상당 한 번뿐인 준비 단계에 파묻혔다 — 빈도가 다른 것을
 * 같은 무게로 놓지 않는다.
 */
export function SourcePage() {
	const params = useParams();
	// 숫자가 아니면 소스일 수 없다 — 서버에 물어보기 전에 여기서 거른다.
	const raw = params.sourceId ?? "";
	const sourceId = /^\d+$/.test(raw) ? Number(raw) : null;

	const studio = useStudioJob();
	const { jobBusy, reloadToken, reload, submit, act } = studio;

	const [criteria, setCriteria] = useState(DEFAULT_CRITERIA);
	const [openPreview, setOpenPreview] = useState<number | null>(null);
	const [transcript, setTranscript] = useState<ShortsUtterance[] | null>(null);
	const [language, setLanguage] = useState<string | null>(null);
	const [showAllRanked, setShowAllRanked] = useState(false);
	const [titleEdit, setTitleEdit] = useState<number | null>(null);
	const [titleDraft, setTitleDraft] = useState("");
	// 2단계에서 분석할 범위(초). null 이면 아직 손대지 않은 상태라 기본값을 그때그때 파생한다 —
	// 영상 길이는 소스를 읽고 나서야 오기 때문에 상태에 미리 복사해 두면 0 으로 굳는다.
	const [range, setRange] = useState<{ start: number; end: number } | null>(null);
	// 조각이 이미 있는데도 범위를 다시 고르는 중인가("다시 추출" 을 누른 뒤).
	const [rangeOpen, setRangeOpen] = useState(false);

	const status = useAsync<ShortsStatus>(fetchStatus, [reloadToken]);
	const detail = useAsync<ShortsSourceDetail | null>(
		() => (sourceId === null ? Promise.resolve(null) : fetchShortsSource(sourceId)),
		[sourceId, reloadToken],
	);
	// 🔴 질문 목록을 패널이 아니라 여기서 읽는다. 탭 배지가 "답을 기다리는 질문 수" 를 보여줘야
	// 하는데, 패널과 따로 읽으면 요청이 두 번 나가고 두 숫자가 서로 어긋난다.
	const clusters = useAsync<ShortsClusterList | null>(
		() => (sourceId === null ? Promise.resolve(null) : fetchClusters(sourceId)),
		[sourceId, reloadToken],
	);

	// 🔴 훅을 전부 부른 뒤에 돌려보낸다. 위에서 return 하면 렌더마다 훅 개수가 달라진다.
	if (sourceId === null) {
		return <NotFound />;
	}

	const data = detail.data;
	const geminiReady = status.data?.geminiKey === true;
	// 🔴 서버가 이 소스에 잠금을 걸고 긴 작업을 돌리는 중. 이 탭이 띄운 잡(jobBusy)과 다르다 —
	// 다른 탭이나 CLI 가 돌린 것도 여기 잡힌다. 전사 중에 조각을 지우면 그 전사가 외래키 위반으로
	// 죽어서(2026-09-06에 당했다) 소스를 건드리는 버튼은 이 값으로도 잠근다.
	const sourceBusy = data?.busy === true;

	// 🔴 원본이 몇 조각으로 나뉘는지는 사용자의 선택이 아니라 **메모리 상한**이다 — 95분을 한 번에
	// 전사하면 컨테이너 한도를 넘어 죽어서, 서버가 길이를 보고 25분 안팎으로 자른다. 그래서 화면은
	// 조각을 단위로 다루지 않고 전부 소스 하나로 합쳐 보여준다. 조각은 구현 사정일 뿐이다.
	const chunks = data?.chunks ?? [];
	const hasChunks = chunks.length > 0;
	const utterances = chunks.reduce((n, c) => n + c.utteranceCount, 0);
	// 전사가 조각 하나에서 끊길 수 있다. 다 됐을 때만 3단계를 끝난 것으로 본다.
	const sttDone = hasChunks && chunks.every((c) => c.utteranceCount > 0);
	// 준비된 분량. 조각 경계는 겹치지 않으므로 합이 곧 영상 전체 길이다.
	const coveredSec = chunks.reduce((n, c) => n + (c.end_sec - c.start_sec), 0);
	// 초 단위로 다룬다 — duration_sec 은 소수점이 붙어 올 수 있고, 그대로 두면 "0분 0.4초" 가 뜬다.
	const duration = Math.round(data?.source.duration_sec ?? 0);
	// 추출된 범위. 조각 경계는 겹치지 않으므로 처음과 끝만 보면 원래 고른 범위가 나온다.
	const chunkStart = hasChunks ? Math.min(...chunks.map((c) => c.start_sec)) : 0;
	const chunkEnd = hasChunks ? Math.max(...chunks.map((c) => c.end_sec)) : 0;
	// 조각이 없으면 편집이 기본 상태다 — 첫 추출은 범위를 정하는 일 그 자체다.
	const editingRange = !hasChunks || rangeOpen;
	const rangeStart = Math.round(range?.start ?? 0);
	// 끝의 기본값은 영상 전체다. 대부분은 이대로 한 번 누르면 끝난다.
	const rangeEnd = Math.round(range?.end ?? duration);
	// 길이를 아직 모르면(0) 상한을 걸지 않는다 — 모르는 값으로 사용자를 막지 않는다.
	const rangeValid = rangeStart < rangeEnd && (duration === 0 || rangeEnd <= duration);
	// 구간 번호(idx)는 소스 안에서 연속이라(segmentation) 조각을 넘어서도 idx 순이 곧 시간 순이다.
	const segments: ShortsSegment[] = chunks.flatMap((c) => c.segments).sort((a, b) => a.idx - b.idx);
	const latestRun = data?.runs[0] ?? null;
	const ranked = latestRun?.ranked?.ranked ?? [];
	const excluded = latestRun?.ranked?.excluded ?? [];
	const clipBySegment = new Map<number, ShortsClip>();
	for (const clip of data?.clips ?? []) {
		const segment = segments.find((s) => s.id === clip.segment_id);
		if (segment) {
			clipBySegment.set(segment.idx, clip);
		}
	}

	// 클립을 출처별로 묶는다. 질문에서 나온 것은 그 질문끼리, 기준에서 나온 것은 기준 문장끼리.
	// 순서는 clips 가 온 순서(최신 먼저)를 따른다 — 방금 만든 것이 위에 있어야 한다.
	const clipGroups: ClipGroup[] = [];
	for (const clip of data?.clips ?? []) {
		const key = clip.question ? `q:${clip.question}` : `c:${clip.criteria_prompt ?? ""}`;
		const found = clipGroups.find((g) => g.key === key);
		if (found) {
			found.clips.push(clip);
		} else {
			clipGroups.push({
				key,
				question: clip.question,
				criteria: clip.criteria_prompt,
				askedBy: clip.asked_by,
				clips: [clip],
			});
		}
	}

	// 다음에 눌러야 할 단계 하나만 강조한다.
	const nextStep = !data ? 1 : !hasChunks ? 2 : !sttDone ? 3 : segments.length === 0 ? 4 : 5;
	const prepDone = hasChunks && sttDone && segments.length > 0;
	// 답을 기다리는 질문. 질문 탭에 갈 이유가 곧 이 숫자다.
	const openCount = clusters.data?.clusters.filter((c) => c.status === "OPEN").length ?? 0;

	const view: SourceView = {
		sourceId,
		data,
		status,
		clusters,
		geminiReady,
		sourceBusy,
		jobBusy,
		submit,
		act,
		reload,
		chunks,
		hasChunks,
		utterances,
		sttDone,
		coveredSec,
		duration,
		chunkStart,
		chunkEnd,
		segments,
		latestRun,
		ranked,
		excluded,
		clipBySegment,
		clipGroups,
		nextStep,
		prepDone,
		editingRange,
		rangeStart,
		rangeEnd,
		rangeValid,
		setRange,
		setRangeOpen,
		criteria,
		setCriteria,
		language,
		setLanguage,
		openPreview,
		setOpenPreview,
		showAllRanked,
		setShowAllRanked,
		titleEdit,
		setTitleEdit,
		titleDraft,
		setTitleDraft,
		transcript,
		setTranscript,
	};

	return (
		<div className="page">
			{/* 브라우저 뒤로가기로도 돌아가지만, 링크로 열린 화면에는 그 길이 없다. */}
			<Link to="/" className="sm-back">
				← 영상 목록
			</Link>

			<StudioHead
				title={data ? data.source.title : "불러오는 중…"}
				count={
					data && `발화 ${utterances} · 구간 ${segments.length} · 클립 ${data.clips.length}`
				}
			/>

			{/* 배너는 탭 위에 둔다 — 한 탭에서 띄운 잡은 탭을 옮겨도 계속 돌고 있다. */}
			<StudioBanners status={status} studio={studio} />

			{detail.error && (
				<>
					<p className="state state--error">
						이 영상을 찾을 수 없습니다. ({detail.error.message})
					</p>
					<p className="sm-meta" style={{ textAlign: "center" }}>
						<Link to="/">영상 목록으로 돌아가기</Link>
					</p>
				</>
			)}

			{/* 무엇을 뽑을지 정하려면 원본을 봐야 한다. 자체 <video> 가 아니라 유튜브 임베드다 —
			    여기서의 시청도 실제 유튜브 시청 시간이 된다(tease §8-3).

			    🔴 접을 수 있어야 한다. 95분짜리 영상의 플레이어가 세로를 크게 먹는데, 답을 쓰는
			    동안 "영상이 실제로 뭐라고 하는지" 를 확인해야 해서 없앨 수도 없다. 기본은 펼침. */}
			{data &&
				(data.source.youtube_id ? (
					<details className="sm-fold sm-playerfold" open>
						<summary>{data.source.title}</summary>
						<div className="sm-player">
							<iframe
								src={`https://www.youtube.com/embed/${data.source.youtube_id}`}
								title={data.source.title}
								allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
								allowFullScreen
							/>
						</div>
					</details>
				) : (
					<p className="sm-meta">임베드할 수 없는 영상입니다</p>
				))}
			{data?.source.channel && <p className="sm-meta">{data.source.channel}</p>}

			{/* 🔴 to 는 절대경로다. 이 화면 자체가 splat 라우트(`sources/:sourceId/*`) 안이라
			    상대경로는 splat 구간까지 붙는다 — /sources/3/clips 에서 to="questions" 는
			    /sources/3/clips/questions 가 된다(react-router 7 의 v7_relativeSplatPath 기본값). */}
			<nav className="sm-tabs">
				<NavLink to={`/sources/${sourceId}/questions`} className={tabClass}>
					질문
					{openCount > 0 && <span className="sm-tab__badge">{openCount}</span>}
				</NavLink>
				<NavLink to={`/sources/${sourceId}/clips`} className={tabClass}>
					클립
				</NavLink>
				<NavLink to={`/sources/${sourceId}/prepare`} className={tabClass}>
					영상 준비
					{!prepDone && <span className="sm-tab__need">준비 필요</span>}
				</NavLink>
				<NavLink to={`/sources/${sourceId}/log`} className={tabClass}>
					기록
				</NavLink>
			</nav>

			<Routes>
				{/* 탭이 없는 /sources/3 은 예전 링크와 북마크다. 매일 쓰는 질문 탭으로 보낸다. */}
				<Route index element={<Navigate to="questions" replace />} />
				<Route path="questions" element={<QuestionsTab view={view} />} />
				<Route path="clips" element={<ClipsTab view={view} />} />
				<Route path="prepare" element={<PrepareTab view={view} />} />
				<Route path="log" element={<LogTab view={view} />} />
				{/* 주소를 손으로 고쳤을 때 탭만 있고 내용이 없는 화면을 만들지 않는다.
				    🔴 여기서 상대경로를 쓰면 /sources/3/bogus/questions 로 가고 그 주소가 다시
				    이 라우트에 걸려 무한 리다이렉트가 된다. */}
				<Route path="*" element={<Navigate to={`/sources/${sourceId}/questions`} replace />} />
			</Routes>
		</div>
	);
}

/** 주소를 손으로 고쳤거나 지워진 영상이다. 막다른 화면을 만들지 않고 목록으로 되돌린다. */
function NotFound() {
	return (
		<div className="page">
			<p className="state">찾을 수 없습니다.</p>
			<p className="sm-meta" style={{ textAlign: "center" }}>
				<Link to="/">영상 목록으로 돌아가기</Link>
			</p>
		</div>
	);
}
