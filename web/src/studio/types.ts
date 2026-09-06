// shorts_maker API 응답 타입.
//
// 이름에서 `Shorts` 접두사를 떼지 않았다 — 파이프라인 개념(Source/Segment/Clip/Run)이
// 워낙 일반적인 단어라 접두사가 없으면 무엇의 Source 인지 읽는 쪽이 헷갈린다.
//
// 🔴 스네이크 케이스와 카멜 케이스가 섞여 있는 건 실수가 아니다. DB 행을 그대로 내보내는
// 필드(`start_sec`)는 스네이크, 파이썬이 계산해서 만든 필드(`utteranceCount`)는 카멜이다.
// 서버가 주는 그대로 적는다 — 프론트에서 변환하면 어느 쪽이 정본인지 알 수 없게 된다.

export interface ShortsStatus {
	ok: boolean;
	schema: number;
	geminiKey: boolean;
	whisperModel: string;
	languages: Record<string, string>;
	activeJob: ShortsJob | null;
}

export interface ShortsJob {
	id: string;
	createdAt: string;
	kind: string;
	target: string;
	status: "QUEUED" | "RUNNING" | "DONE" | "FAILED";
	error: string | null;
}

export interface ShortsSource {
	id: number;
	title: string;
	content_type: string;
	language: string | null;
	duration_sec: number | null;
	origin: string | null;
	status: string;
	/** 🔴 시청자 화면(/watch)에 이 영상이 보이는가. false 면 공개 API 에서 404 다. */
	published: boolean;
	/** 유튜브에서 받았으면 영상 id. 없으면 시청자 화면이 임베드 플레이어를 못 띄운다. */
	youtube_id: string | null;
	channel: string | null;
}

export interface ShortsSegment {
	id: number;
	idx: number;
	start_sec: number;
	end_sec: number;
	description: string | null;
	excluded_by: string | null;
	excluded_reason: string | null;
}

export interface ShortsChunk {
	id: number;
	idx: number;
	start_sec: number;
	end_sec: number;
	utteranceCount: number;
	segments: ShortsSegment[];
}

export interface ShortsRun {
	id: number;
	status: string;
	criteria_prompt: string | null;
	error: string | null;
	ranked: {
		ranked?: { idx: number; score: number; reason: string }[];
		excluded?: { idx: number; reason: string }[];
	} | null;
}

export interface ShortsClipReview {
	id: number;
	verdict: "OK" | "NG";
	note: string | null;
}

export interface ShortsClip {
	id: number;
	segment_id: number;
	start_sec: number;
	end_sec: number;
	score: number | null;
	reason: string | null;
	// Postgres boolean(2026-09-06). 예전 SQLite 는 0/1 이었다.
	rendered: boolean;
	description: string | null;
	reviews: ShortsClipReview[];
}

export interface ShortsStageCall {
	id: number;
	stage: string;
	model: string | null;
	input_tokens: number | null;
	output_tokens: number | null;
	thinking_tokens: number | null;
	latency_ms: number | null;
	error: string | null;
}

export interface ShortsUtterance {
	idx: number;
	start_sec: number;
	end_sec: number;
	text: string;
	avg_logprob: number | null;
}

export interface ShortsCost {
	llmCalls: number;
	inputTokens: number;
	outputTokens: number;
	thinkingTokens: number;
	billedOutputTokens: number;
	usd: number;
	krw: number;
	rate: {
		inputUsdPer1M: number;
		outputUsdPer1M: number;
		usdKrw: number;
		model: string;
	};
}

export interface ShortsMediaItem {
	name: string;
	sizeBytes: number;
	sourceId: number | null;
	title: string | null;
	durationSec: number | null;
}

export interface ShortsDisk {
	totalBytes: number;
	usedBytes: number;
	freeBytes: number;
	sourcesBytes: number;
	workBytes: number;
}

export interface ShortsMediaList {
	items: ShortsMediaItem[];
	disk: ShortsDisk;
}

export interface ShortsRemoval {
	removed: string[];
	freedBytes: number;
	sourceId: number | null;
}

export interface ShortsSourceDetail {
	/**
	 * 🔴 이 소스에 긴 작업(구간 추출·전사·주제 분할)이 물려 있는가. 서버의 advisory lock 을
	 * 그대로 읽은 값이라 다른 탭·CLI 가 돌린 작업까지 잡힌다. 화면이 버튼을 잠그는 근거다.
	 */
	busy: boolean;
	source: ShortsSource;
	chunks: ShortsChunk[];
	runs: ShortsRun[];
	clips: ShortsClip[];
	cost: ShortsCost;
	stageCalls: ShortsStageCall[];
}

// ---------------------------------------------------------------- 시청자 질문 묶기

/** 질문 원문 하나. 좋아요는 클러스터가 아니라 질문에 붙어서 재집계에도 보존된다. */
export interface ShortsClusterQuestion {
	id: number;
	text: string;
	created_at: string;
	// SQL 이 센 좋아요 수(question_likes 조인). 서버가 계산해 주므로 화면에서 다시 세지 않는다.
	likes: number;
}

/** 묶인 질문 그룹 = 숏폼 하나의 작업 단위(tease §3-2). `question_clusters` 행 그대로에 수요가 붙는다. */
export interface ShortsCluster {
	id: number;
	source_id: number;
	canonical_text: string;
	status: "OPEN" | "IN_PROGRESS" | "REVIEW" | "PUBLISHED" | "DECLINED" | "UNANSWERABLE";
	/** 이 클러스터에 답하려고 띄운 run. 클립이 나오기 전에도 진행을 보여주려고 둔다. */
	run_id: number | null;
	/** UNANSWERABLE 인데 다른 영상에 답이 있어 보일 때 그 영상. */
	suggested_source_id: number | null;
	created_at: string;
	updated_at: string;
	// 🔴 `c.*` 에 얹힌 SQL 집계라 스네이크다(answers/clusters.demand). 파이썬이 계산한 게 아니다.
	question_count: number;
	like_count: number;
	questions: ShortsClusterQuestion[];
	/** 이 묶음에 답한 클립. 아직 [답하기] 를 안 눌렀거나 컷이 안 나왔으면 null 이다. */
	clip: ShortsClusterClip | null;
}

/**
 * 묶음 하나에 답한 클립 + judge 소견(answers/clusters.clip_of). 묶음당 하나이며, 다시 답하면
 * 새 run 의 클립으로 갈린다.
 *
 * 🔴 `llmVerdict`/`llmNote` 가 여기 실린 이유는 크리에이터가 **발행 전에** 봐야 하기 때문이다 —
 * 자립하지 않는다고 판정된 클립이 조용히 발행되면 시청자가 먼저 발견한다.
 */
export interface ShortsClusterClip {
	id: number;
	rendered: boolean;
	/** 발행 시각. null 이면 아직 시청자에게 안 보인다. */
	published_at: string | null;
	total_sec: number | null;
	reason: string | null;
	score: number | null;
	run_id: number;
	// clip_reviews 의 최신 llm 행을 파이썬이 두 필드로 접어 만든 값이라 카멜이다.
	llmVerdict: "OK" | "NG" | null;
	llmNote: string | null;
	/** 조각이 둘 이상이면 흩어진 구간을 이어붙인 클립이다 — 이 제품의 눈에 보이는 차별점. */
	parts: { ordinal: number; start_sec: number; end_sec: number }[];
}

export interface ShortsClusterList {
	/** 서버가 수요 순(질문 수 → 좋아요 순)으로 준다. 프론트에서 다시 정렬하지 않는다. */
	clusters: ShortsCluster[];
	/** 아직 어느 그룹에도 안 붙은 질문. 이게 0 이면 [집계] 를 눌러도 할 일이 없다. */
	unclustered: ShortsClusterQuestion[];
}
