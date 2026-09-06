import { request } from "./client";
import type {
	ShortsClip,
	ShortsCost,
	ShortsJob,
	ShortsMediaList,
	ShortsRemoval,
	ShortsSource,
	ShortsSourceDetail,
	ShortsStatus,
	ShortsUtterance,
} from "../types";

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

// 🔴 replace: 기존 청크와 그 아래 전부(발화·구간·클립·run)를 서버가 지우고 다시 만든다.
// LECTURE 는 소스당 청크 1개라 "다시 추출"은 추가가 아니라 교체다.
export function createChunk(
	sourceId: number,
	startSec: number,
	endSec: number,
	replace = false,
): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/sources/${sourceId}/chunks`, {
		method: "POST",
		body: { startSec, endSec, replace },
	});
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

export function runStt(chunkId: number, language: string | null, force = true): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/chunks/${chunkId}/stt`, {
		method: "POST",
		body: { force, language },
	});
}

export function runSegment(chunkId: number, force = true): Promise<ShortsJob> {
	return request<ShortsJob>(`/api/chunks/${chunkId}/segment?force=${force}`, { method: "POST" });
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
