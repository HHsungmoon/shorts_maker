import type { Dispatch, SetStateAction } from "react";
import { useState } from "react";
import { Link, Navigate, Route, Routes, useParams } from "react-router-dom";
import { fetchClusters, fetchShortsSource, fetchStatus } from "./api";
import { LogTab } from "./components/LogTab";
import { NewClipTab } from "./components/NewClipTab";
import { PublishTab } from "./components/PublishTab";
import { ReportTab } from "./components/ReportTab";
import { PrepareTab } from "./components/PrepareTab";
import { QuestionsTab } from "./components/QuestionsTab";
import { MiniPlayerDock, SourceBar } from "./components/SourceBar";
import type { MiniPlayer } from "./components/SourceBar";
import { StudioBanners } from "./components/StudioChrome";
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

// 🔴 빈 칸으로 시작한다. 예전엔 "한 문장으로 인용할 만한 핵심 논지" 가 **값**으로 채워져 있어서
// 입력 안내처럼 보이는데 실제로는 그대로 기준으로 나갔다. 안내 문구는 입력칸의 placeholder 가 맡는다.
const DEFAULT_CRITERIA = "";

type RankedPayload = NonNullable<ShortsRun["ranked"]>;
export type RankedEntry = NonNullable<RankedPayload["ranked"]>[number];
export type ExcludedEntry = NonNullable<RankedPayload["excluded"]>[number];

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
	/**
	 * 이 탭이 띄운 잡. 대부분의 탭은 `jobBusy` 로 충분하지만, **결과를 DB 가 아니라 잡에서
	 * 받아오는** 자리가 있어서(개요 초안) 잡 자체가 필요하다.
	 */
	job: ShortsJob | null;
	submit: (start: () => Promise<ShortsJob>) => void;
	act: (run: () => Promise<unknown>) => void;
	reload: () => void;
	/** 오른쪽 아래 미니 플레이어로 원본을 이 초부터 튼다. 후보가 원본 어디서 왔는지 확인하는 길이다. */
	playAt: (sec: number) => void;

	chunks: ShortsChunk[];
	hasChunks: boolean;
	utterances: number;
	sttDone: boolean;
	coveredSec: number;
	duration: number;
	chunkStart: number;
	chunkEnd: number;
	segments: ShortsSegment[];
	/** 새 클립 탭의 선정 run 만, 최근 것이 앞. 지난 기준을 다시 꺼내 쓰는 데 쓴다. */
	criteriaRuns: ShortsRun[];
	/** 가장 최근 선정 run. 질문 답하기의 run 은 들어오지 않는다. */
	latestRun: ShortsRun | null;
	ranked: RankedEntry[];
	excluded: ExcludedEntry[];
	clipBySegment: Map<number, ShortsClip>;
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
	const { job, jobBusy, reloadToken, reload, submit, act } = studio;

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
	// 미니 플레이어. 어느 영상의 것인지도 함께 든다 — 다른 영상으로 옮기면 앞 영상에서 고른 초로
	// 새 영상을 틀지 않게 한다.
	const [player, setPlayer] = useState<(MiniPlayer & { sourceId: number }) | null>(null);

	// 🔴 셋 다 `keepPrevious` 다. 잡이 도는 동안 `reloadToken` 이 올라가며 통째로 다시 읽는데,
	// 유지하지 않으면 그때마다 화면이 비어 작업이 사라진 것처럼 보인다. `subject` 를 주는 이유는
	// **다른 영상으로 옮길 때는 비워야** 하기 때문이다 — 안 그러면 옛 영상의 내용이 잠깐 남는다.
	const status = useAsync<ShortsStatus>(fetchStatus, [reloadToken], { keepPrevious: true });
	const detail = useAsync<ShortsSourceDetail | null>(
		() => (sourceId === null ? Promise.resolve(null) : fetchShortsSource(sourceId)),
		[sourceId, reloadToken],
		{ keepPrevious: true, subject: sourceId },
	);
	// 🔴 질문 목록을 패널이 아니라 여기서 읽는다. 탭 배지가 "답을 기다리는 질문 수" 를 보여줘야
	// 하는데, 패널과 따로 읽으면 요청이 두 번 나가고 두 숫자가 서로 어긋난다.
	const clusters = useAsync<ShortsClusterList | null>(
		() => (sourceId === null ? Promise.resolve(null) : fetchClusters(sourceId)),
		[sourceId, reloadToken],
		{ keepPrevious: true, subject: sourceId },
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
	// 🔴 runs 에는 질문 답하기의 run 도 섞여 온다(최근 것이 앞). 그대로 runs[0] 을 쓰면 질문에 한 번
	// 답한 순간 새 클립 탭의 선정 결과가 통째로 사라졌다(2026-10-24 발견). 선정 run 만 고른다.
	const criteriaRuns = data?.runs.filter((run) => run.prompt !== null) ?? [];
	const latestRun = criteriaRuns[0] ?? null;
	const ranked = latestRun?.ranked?.ranked ?? [];
	const excluded = latestRun?.ranked?.excluded ?? [];
	const clipBySegment = new Map<number, ShortsClip>();
	for (const clip of data?.clips ?? []) {
		const segment = segments.find((s) => s.id === clip.segment_id);
		if (segment) {
			clipBySegment.set(segment.idx, clip);
		}
	}

	// 다음에 눌러야 할 단계 하나만 강조한다.
	const nextStep = !data ? 1 : !hasChunks ? 2 : !sttDone ? 3 : segments.length === 0 ? 4 : 5;
	const prepDone = hasChunks && sttDone && segments.length > 0;
	// 답을 기다리는 질문. 질문 탭에 갈 이유가 곧 이 숫자다.
	const openCount = clusters.data?.clusters.filter((c) => c.status === "OPEN").length ?? 0;
	// 🔴 리포트 배지는 "이 영상에 답이 없는 질문" 수다. 이미 읽어 둔 목록에서 센다 —
	// 배지 하나 때문에 요청을 하나 더 내보내지 않는다.
	const missingCount =
		clusters.data?.clusters.filter((c) => c.status === "UNANSWERABLE").length ?? 0;

	function playAt(sec: number) {
		if (sourceId === null) {
			return;
		}
		const id = sourceId;
		setPlayer((prev) => ({ sourceId: id, start: Math.max(0, Math.floor(sec)), nonce: (prev?.nonce ?? 0) + 1 }));
	}

	const view: SourceView = {
		sourceId,
		data,
		status,
		clusters,
		geminiReady,
		sourceBusy,
		jobBusy,
		job,
		submit,
		act,
		reload,
		playAt,
		chunks,
		hasChunks,
		utterances,
		sttDone,
		coveredSec,
		duration,
		chunkStart,
		chunkEnd,
		segments,
		criteriaRuns,
		latestRun,
		ranked,
		excluded,
		clipBySegment,
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
		<div className="sm-shell">
			<SourceBar
				sourceId={sourceId}
				data={data}
				utterances={utterances}
				segments={segments.length}
				prepDone={prepDone}
				openCount={openCount}
				missingCount={missingCount}
				onPlay={playAt}
			/>

			<main className="sm-shell__main">
					{/* 배너는 탭 내용 위에 둔다 — 한 탭에서 띄운 잡은 탭을 옮겨도 계속 돌고 있다.
				    보기 전용 표시는 상단바가 늘 보여주므로 여기서는 뺀다. */}
				<StudioBanners status={status} studio={studio} readonlyNotice={false} />

				{/* 🔴 오류를 둘로 나눈다. 이전 데이터를 들고 있게 되면서(useAsync keepPrevious) 잡이 도는
				    동안의 일시적인 실패와 "정말 없는 영상" 이 같은 문구로 나올 수 있게 됐다 —
				    내용이 멀쩡히 보이는데 "영상을 찾을 수 없습니다" 가 뜨면 그게 거짓말이다. */}
				{detail.error && !data && (
					<>
						<p className="state state--error">
							이 영상을 찾을 수 없습니다. ({detail.error.message})
						</p>
						<p className="sm-meta" style={{ textAlign: "center" }}>
							<Link to="/studio">영상 목록으로 돌아가기</Link>
						</p>
					</>
				)}
				{detail.error && data && (
					// 화면은 그대로 두고 알리기만 한다. 다음 폴링이 성공하면 조용히 사라진다.
					<p className="sm-meta" style={{ textAlign: "center" }}>
						잠시 갱신하지 못했습니다. 화면은 마지막으로 읽은 내용입니다. ({detail.error.message})
					</p>
				)}

				<Routes>
					{/* 탭이 없는 /sources/3 은 예전 링크와 북마크다. 매일 쓰는 질문 탭으로 보낸다. */}
					<Route index element={<Navigate to="questions" replace />} />
					<Route path="questions" element={<QuestionsTab view={view} />} />
					<Route path="prepare" element={<PrepareTab view={view} />} />
					<Route path="make" element={<NewClipTab view={view} />} />
					<Route path="clips" element={<PublishTab view={view} />} />
					<Route path="report" element={<ReportTab view={view} />} />
					<Route path="log" element={<LogTab view={view} />} />
					{/* 주소를 손으로 고쳤을 때 탭만 있고 내용이 없는 화면을 만들지 않는다.
					    🔴 여기서 상대경로를 쓰면 /sources/3/bogus/questions 로 가고 그 주소가 다시
					    이 라우트에 걸려 무한 리다이렉트가 된다. */}
					<Route path="*" element={<Navigate to={`/studio/sources/${sourceId}/questions`} replace />} />
				</Routes>
			</main>

			{player?.sourceId === sourceId && data?.source.youtube_id && (
				<MiniPlayerDock
					youtubeId={data.source.youtube_id}
					title={data.source.title}
					player={player}
					onClose={() => setPlayer(null)}
				/>
			)}
		</div>
	);
}

/** 주소를 손으로 고쳤거나 지워진 영상이다. 막다른 화면을 만들지 않고 목록으로 되돌린다. */
function NotFound() {
	return (
		<div className="page">
			<p className="state">찾을 수 없습니다.</p>
			<p className="sm-meta" style={{ textAlign: "center" }}>
				<Link to="/studio">영상 목록으로 돌아가기</Link>
			</p>
		</div>
	);
}
