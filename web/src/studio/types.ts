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

/**
 * 목록(GET /api/sources)이 주는 행 = `sources` 행 + **어디까지 왔는지** 세어 준 집계.
 *
 * 🔴 상세(GET /api/sources/{id})의 `source` 에는 이 집계가 없다 — 그래서 ShortsSource 에 얹지 않고
 * 목록 전용 타입으로 갈랐다. 한 타입으로 합치면 상세 화면에서 `chunk_count` 를 읽을 수 있어 보이는데
 * 실제로는 undefined 다.
 */
export interface ShortsSourceListItem extends ShortsSource {
	chunk_count: number;
	utterance_count: number;
	segment_count: number;
	question_count: number;
	/** 🔴 아직 답하지 않은 질문 묶음. 홈에서 이 영상을 열 이유가 바로 이 숫자다(tease §3). */
	open_cluster_count: number;
	clip_count: number;
	published_clip_count: number;
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
	/** 🔴 null 이면 시청자에게 보이지 않는다. 이 컬럼 하나가 공개 여부의 전부다. */
	published_at: string | null;
	/** 조각 합. 조합 클립에서는 start/end 가 봉투라 이쪽이 실제 길이다. */
	total_sec: number | null;
	/** 시청자 목록에 보이는 제목. 기본값은 질문(답하기) 또는 구간 설명(기준)이고 고칠 수 있다. */
	title: string | null;
	/** 🔴 이 클립이 어디서 나왔나. 질문에서 나왔으면 그 대표 문장, 기준에서 나왔으면 null. */
	question: string | null;
	/** 그 질문을 몇 명이 물었나. 질문에서 나온 클립만 의미가 있다. */
	asked_by: number;
	/** 이 클립을 만든 run 의 기준 문장. 질문에서 나온 클립은 대표 문장과 같다. */
	criteria_prompt: string | null;
	route: string | null;
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
	/**
	 * 프롬프트나 원본 응답이 저장돼 있는가.
	 *
	 * 🔴 본문 자체는 목록에 오지 않는다 — 한 건이 수십 KB 라 화면 한 번에 수 MB 가 실린다.
	 * 펼칠 때 `fetchStageCallBody` 로 따로 읽는다. 이 컬럼이 생긴 뒤의 호출만 값이 있다.
	 */
	has_body: boolean;
}

/** 한 호출의 프롬프트와 원본 응답. 목록 행을 펼칠 때만 읽는다. */
export interface ShortsStageCallBody {
	id: number;
	stage: string;
	model: string | null;
	created_at: string;
	prompt: string | null;
	response: string | null;
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
	/**
	 * 🔴 run 이 남긴 사유. `답할 구간 없음` 배지만으로는 "영상이 정말 안 다룬다"와 "검색이 엉뚱한
	 * 데를 봤다"를 구분할 수 없다 — 이유는 이미 저장돼 있으니 화면에 꺼내 놓는다.
	 */
	run_note: string | null;
	/** run 이 실패했을 때의 오류. note 와 달리 이건 정상 결과가 아니다. */
	run_error: string | null;
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

/**
 * 숏폼 하나의 퍼널 (tease §9, `GET /api/sources/{id}/insights`).
 *
 * 🔴 값은 **사람 수**다(이벤트 수가 아니다). 같은 사람이 세 번 돌려 봐도 1이다 —
 * 근거는 backend `answers/events.py::funnel` 주석.
 */
export interface ShortsFunnelRow {
	clip_id: number;
	cluster_id: number | null;
	published_at: string;
	total_sec: number | null;
	/** 목록에 보이는 이름. 크리에이터가 고친 제목이 있으면 그것, 없으면 질문 대표 문장. */
	label: string | null;
	/** 질문에서 나온 숏폼만 값이 있다. null 이면 크리에이터가 자기 기준으로 뽑은 것이다. */
	question: string | null;
	short_play: number;
	short_complete: number;
	cta_click: number;
	origin_seek: number;
	origin_play: number;
}

export interface ShortsInsights {
	/** 퍼널 칸의 순서. 서버가 정하고 화면은 그대로 그린다 — 두 곳에서 순서를 정하면 어긋난다. */
	kinds: string[];
	clips: ShortsFunnelRow[];
	totals: {
		viewers: number;
		question_post: number;
		like: number;
		short_play: number;
		short_complete: number;
		cta_click: number;
		origin_seek: number;
		origin_play: number;
	};
}
