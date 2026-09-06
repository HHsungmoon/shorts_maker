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
