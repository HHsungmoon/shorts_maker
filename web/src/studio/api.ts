import { request } from "../shared/client";
import type {
	ShortsClip,
	ShortsCluster,
	ShortsClusterList,
	ShortsCost,
	ShortsJob,
	ShortsMediaList,
	ShortsRemoval,
	ShortsSource,
	ShortsSourceDetail,
	ShortsStatus,
	ShortsUtterance,
} from "./types";

// 경로가 admin-web 시절의 `/admin/shorts/**` 에서 `/api/**` 로 바뀌었다 — 그 접두사는
// Spring 프록시가 붙이던 것이고, 이제 shorts_maker 를 직접 부른다.

export function fetchStatus(): Promise<ShortsStatus> {
	return request<ShortsStatus>("/api/status");
}

export function fetchMedia(): Promise<ShortsMediaList> {
	return request<ShortsMediaList>("/api/media");
}

// 🔴 서버에서 파일과 파생물(청크·미리보기·클립)을 실제로 지운다. 되돌릴 수 없다.
export function deleteMedia(name: string): Promise<ShortsRemoval> {
	return request<ShortsRemoval>(`/api/media/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export function registerMedia(
	name: string,
	title: string,
	language: string | null,
): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/media/${encodeURIComponent(name)}/register`, {
		method: "POST",
		body: { title, language },
	});
}

export function createSourceFromUrl(url: string, language: string | null): Promise<ShortsJob> {
	return request<ShortsJob>("/api/sources/from-url", { method: "POST", body: { url, language } });
}

/**
 * 분석할 **범위**. 사용자가 정하는 건 여기까지고, 그 안을 몇 조각으로 나눌지는 서버가 정한다 —
 * 조각 수는 취향이 아니라 메모리 상한이다(ingest.plan_chunks). 시작·끝을 비우면 영상 전체다.
 */
export interface ChunkRange {
	startSec?: number;
	endSec?: number;
	// 🔴 기존 조각과 그 아래 전부(발화·구간·클립·run)를 서버가 지우고 다시 만든다.
	// 화면의 "다시 추출"이 이것이다. 켜지 않고 이미 있으면 409 다.
	replace?: boolean;
}

// 몇 조각이 됐는지는 잡 결과의 `chunks` 로만 돌아온다.
export function createChunk(sourceId: number, range: ChunkRange = {}): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/sources/${sourceId}/chunks`, {
		method: "POST",
		body: { replace: false, ...range },
	});
}

// 🔴 발행하면 이 영상이 로그인 없는 시청자에게 보인다(질문·좋아요 포함). 되돌리려면 unpublish.
export function publishSource(sourceId: number, published: boolean): Promise<{ published: boolean }> {
	const action = published ? "publish" : "unpublish";
	return request<{ published: boolean }>(`/api/sources/${sourceId}/${action}`, { method: "POST" });
}

export function fetchShortsSources(): Promise<ShortsSource[]> {
	return request<ShortsSource[]>("/api/sources");
}

export function fetchShortsSource(sourceId: number): Promise<ShortsSourceDetail> {
	return request<ShortsSourceDetail>(`/api/sources/${sourceId}`);
}

export function fetchUtterances(chunkId: number): Promise<ShortsUtterance[]> {
	return request<ShortsUtterance[]>(`/api/chunks/${chunkId}/utterances`);
}

// 소스 단위다. 조각이 여럿이면 서버가 순서대로 전사하고, 화면에는 잡 하나로 보인다.
export function runStt(sourceId: number, language: string | null, force = true): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/sources/${sourceId}/stt`, {
		method: "POST",
		body: { force, language },
	});
}

// 구간 번호는 소스 안에서 연속이다 — 조각 경계는 여기서 이미 사라진다.
export function runSegment(sourceId: number): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/sources/${sourceId}/segment`, { method: "POST" });
}

export function runRank(sourceId: number, criteria: string | null): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/sources/${sourceId}/rank`, {
		method: "POST",
		body: { criteria },
	});
}

export function runPipeline(
	sourceId: number,
	criteria: string | null,
	resegment = false,
): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/sources/${sourceId}/pipeline?resegment=${resegment}`, {
		method: "POST",
		body: { criteria },
	});
}

export function fetchTotalCost(): Promise<ShortsCost> {
	return request<ShortsCost>("/api/cost");
}

export function runCut(runId: number, segmentId: number, replace = false): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/runs/${runId}/segments/${segmentId}/cut?replace=${replace}`, {
		method: "POST",
	});
}

export function renderClip(clipId: number, force = false): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/clips/${clipId}/render?force=${force}`, { method: "POST" });
}

export function fetchJob(jobId: string): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/jobs/${jobId}`);
}

export function reviewClip(clip: ShortsClip, verdict: "OK" | "NG", note?: string): Promise<void> {
	return request<void>(`/api/clips/${clip.id}/review`, {
		method: "POST",
		body: { verdict, note: note ?? null },
	});
}

// 영상 주소는 <video src> 에 그대로 넣는다.
//
// admin-web 에서는 blob 으로 받아 object URL 로 넘겨야 했다 — 클립이 `/admin/**` 이라
// Authorization 헤더가 필요한데 <video src> 는 헤더를 못 붙이기 때문이었다. 지금은
// 세션 쿠키라 브라우저가 알아서 실어 보낸다. 그 우회가 통째로 사라졌고, 덕분에
// **브라우저가 range 요청으로 스트리밍**한다 — 예전엔 전체를 받아야 재생이 시작됐다.
export function clipUrl(clipId: number): string {
	return `/api/clips/${clipId}/file`;
}

export function segmentPreviewUrl(segmentId: number): string {
	return `/api/segments/${segmentId}/preview`;
}

// ---------------------------------------------------------------- 시청자 질문 묶기

// 🔴 집계는 크리에이터가 누를 때만 돈다(update_plan D11). 질문이 등록될 때마다 자동으로 돌리면
// 로그인 없는 공개 경로가 유료 API 를 부르는 셈이라 그 자체가 공격면이다.
// 재집계는 증분이라 몇 번을 눌러도 기존 소속과 좋아요는 그대로다.
export function aggregateQuestions(sourceId: number): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/sources/${sourceId}/aggregate`, { method: "POST" });
}

export function fetchClusters(sourceId: number): Promise<ShortsClusterList> {
	return request<ShortsClusterList>(`/api/sources/${sourceId}/clusters`);
}

// 응답은 `question_clusters` 행 하나뿐이다 — 수요 집계(question_count/like_count)와 질문 원문은
// 목록 엔드포인트가 조인해서 만드는 값이라 여기엔 없다. 그래서 반환 타입에서 빼두고,
// 화면은 이 값을 쓰지 않고 목록을 다시 읽는다.
export function patchCluster(
	clusterId: number,
	body: { canonicalText?: string; status?: string },
): Promise<Omit<ShortsCluster, "question_count" | "like_count" | "questions">> {
	return request<Omit<ShortsCluster, "question_count" | "like_count" | "questions">>(
		`/api/clusters/${clusterId}`,
		{ method: "PATCH", body },
	);
}
