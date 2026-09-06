import { request } from "../shared/client";

// 시청자 API. 인증이 없다 — 익명 쿠키(sm_viewer)를 서버가 발급하고 브라우저가 자동으로 싣는다.
//
// 🔴 쿠키가 httpOnly 라 여기서 "내가 누구인지" 를 알 수 없다. 그래서 "내가 좋아요 했나"(`liked_by_me`)
// 는 서버가 계산해서 준다.
//
// 스네이크/카멜이 섞인 건 실수가 아니다 — DB 행을 그대로 내보내는 필드는 스네이크, 파이썬이
// 계산해 만든 필드는 카멜이다(studio/types.ts 와 같은 규칙).

export interface WatchSource {
	id: number;
	title: string;
	duration_sec: number | null;
	youtube_id: string | null;
	channel: string | null;
	question_count: number;
	published_clip_count: number;
}

export interface WatchQuestion {
	id: number;
	text: string;
	/** 크리에이터가 [집계]를 돌리기 전에는 null 이다 — 질문 등록은 insert 만 한다. */
	cluster_id: number | null;
	created_at: string;
	likes: number;
	liked_by_me: boolean;
}

export interface WatchClip {
	id: number;
	total_sec: number | null;
	published_at: string;
	question_cluster_id: number | null;
	/** 이 숏폼이 답하는 질문(클러스터 대표 문장). 클러스터 없이 발행된 클립은 null 이다. */
	question: string | null;
	/** 그 클러스터에 묶인 질문 수. 1 이면 "몇 명이 물어봤다"를 말할 이유가 없다. */
	asked_by: number;
}

/** 답할 구간이 없다고 판정된 질문 클러스터. 다른 편을 가리킬 수 있으면 그 영상도 함께 온다. */
export interface WatchUnanswerable {
	id: number;
	question: string;
	suggested_source_id: number | null;
	suggested_title: string | null;
}

export interface WatchDetail {
	source: {
		id: number;
		title: string;
		duration_sec: number | null;
		youtube_id: string | null;
		channel: string | null;
		origin: string | null;
		context: string | null;
	};
	questions: WatchQuestion[];
	clips: WatchClip[];
	unanswerable: WatchUnanswerable[];
}

/**
 * 발행된 숏폼 파일.
 *
 * 🔴 `/api/clips/{id}/file` 이 아니다. 그쪽은 스튜디오 라우트라 세션 인증 뒤에 있고 미발행
 * 클립도 프리뷰로 내준다 — 시청자 브라우저에서는 401 이 난다. 이 경로는 무인증이지만
 * `published_at is not null` 인 클립만 준다(backend `http/watch.py:get_clip_file`).
 */
export function clipFileUrl(clipId: number): string {
	return `/api/watch/clips/${clipId}/file`;
}

export function fetchWatchSources(): Promise<WatchSource[]> {
	return request<WatchSource[]>("/api/watch/sources");
}

export function fetchWatchSource(sourceId: number): Promise<WatchDetail> {
	return request<WatchDetail>(`/api/watch/sources/${sourceId}`);
}

export function postQuestion(sourceId: number, text: string): Promise<{ questionId: number }> {
	return request<{ questionId: number }>(`/api/watch/sources/${sourceId}/questions`, {
		method: "POST",
		body: { text },
	});
}

export function toggleLike(questionId: number): Promise<{ liked: boolean; likes: number }> {
	return request<{ liked: boolean; likes: number }>(`/api/watch/questions/${questionId}/like`, {
		method: "POST",
	});
}

/** 퍼널 계측(§9). 🔴 실패해도 화면을 막지 않는다 — 계측 때문에 사용자가 멈추면 안 된다. */
export function recordEvent(kind: string, sourceId: number, payload?: Record<string, unknown>): void {
	void request("/api/watch/events", { method: "POST", body: { kind, sourceId, payload } }).catch(
		() => undefined,
	);
}

/** 유튜브 썸네일. 저장하지 않고 id 에서 만든다 — 영상이 바뀌면 썸네일도 따라 바뀐다. */
export function thumbnailUrl(youtubeId: string): string {
	return `https://i.ytimg.com/vi/${youtubeId}/hqdefault.jpg`;
}
