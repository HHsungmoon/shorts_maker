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
	/**
	 * 잡이 돌려준 것. 대부분은 DB 에 쓰고 끝나서 화면이 볼 일이 없지만, 저장하지 않고 **초안만**
	 * 돌려주는 잡이 있다(개요 초안). 그건 여기로만 온다.
	 */
	result: unknown;
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
	/**
	 * 지금 발화가 어디서 왔나(tease §5-1). 유튜브 자막이 기본이고 음성 인식은 따로 누른다.
	 * null 은 아직 대사가 없거나, 이 표시가 생기기 전(2026-11-01)에 만든 것이다.
	 */
	caption_source: "youtube" | "whisper" | null;
	/**
	 * 영상 개요(프롬프트 3층). 구간 분할·순위·자르기·후보 생성에 붙는다.
	 * 🔴 이미 나눈 구간에는 반영되지 않는다 — 구간은 캐시된 자산이다.
	 */
	context: string | null;
	/**
	 * 음성 인식 용어 힌트(고유명사). 다음 음성 인식부터 whisper 의 initial_prompt 로 붙는다.
	 * 🔴 이미 끝난 전사에는 반영되지 않는다 — 다시 인식해야 한다.
	 */
	stt_terms: string[];
	/**
	 * 음성 인식 교정 사전. 잘못 들린 말 → 바른 말, 적힌 순서대로 적용한다.
	 * 🔴 힌트와 달리 저장하면 **지금 전사에 바로** 적용된다. 이미 렌더한 클립 자막은 그대로다.
	 */
	stt_fixes: SttFix[];
}

export type SttFix = { from: string; to: string };

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
	/** 이 구간이 들어 있는 처리 조각. 전사(발화)는 조각 단위로 읽는다. */
	chunk_id: number;
	idx: number;
	start_sec: number;
	end_sec: number;
	/** 조각 안 발화 번호 범위(양끝 포함). 구간의 전사 문장을 고를 때 쓴다. */
	start_utterance_idx: number;
	end_utterance_idx: number;
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
	/**
	 * 실제로 보낸 프롬프트 전문. 🔴 **새 클립 탭의 선정(pipeline/ranking)만 채운다** — 질문 답하기의
	 * run(answers/answer)은 비어 있다. 그래서 "이 run 이 선정인가" 를 이 값으로 가른다(SourcePage).
	 */
	prompt: string | null;
	/** 질문 답하기가 고른 길('retrieval' | 'rank'). 선정 run 은 null. */
	route: string | null;
	created_at: string;
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
	/**
	 * 누가 매겼나. 🔴 사람 평가와 모델 판정이 **같은 목록**에 온다(최신순). "지금 사람 평가" 를 보이려면
	 * `human` 중 첫 행을 쓴다 — 전부 이어 붙이면 모델 판정이 사람 평가처럼 읽힌다.
	 */
	reviewer: "human" | "llm";
	created_at: string;
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
	/** 이 클립을 유튜브에 올린 기록, 최신 먼저. 같은 클립을 비공개로 시험한 뒤 다시 올릴 수 있어 여럿이다. */
	uploads: ShortsClipUpload[];
	/** 숏폼 편집 명세(tease §5-10). null 이면 예전의 블러 렌더다. */
	layout: ClipLayout | null;
	/** 마지막 렌더에 쓴 명세의 사본. 카드로 구운 적이 없으면 null. */
	render_spec: { rendered_at?: string } | null;
}

/** 누가 정한 값인가. 🔴 person 인 값은 AI 를 다시 돌려도 덮지 않는다(P5). */
export type LayoutBy = "auto" | "ai" | "person";

/**
 * 원본에서 칸에 보여 줄 영역. x · y · w 는 원본 폭 · 높이에 대한 비율이고, 높이는 칸 비율에서 나온다.
 * 장면마다 하나다 — start 는 클립 안의 초(조각을 이은 시간, 브릿지 제외), end 는 다음 장면의 start(서버가 채운다).
 */
export interface ClipWindow {
	start: number;
	end: number | null;
	x: number;
	y: number;
	w: number;
	by: LayoutBy;
}

/** 클립 편집 명세(clips.layout). 모양 검사는 서버(pipeline/layout.py)가 한다. */
export interface ClipLayout {
	version: number;
	template_id: number;
	series_id: number | null;
	headline: string[];
	credit: string;
	subtitles: boolean;
	subtitles_by: LayoutBy;
	slot_height: number;
	windows: ClipWindow[];
	/** 사람이 고친 자막 문구(tease §5-10 "자막 고치기"). 줄의 원본 시각(at)과 원래 문구(was)가 열쇠다. */
	subtitle_edits: SubtitleEdit[];
}

export interface SubtitleEdit {
	/** 줄의 원본 시각 — 첫 단어의 시작(원본 절대 초). */
	at: number;
	/** 원래 문구. 대사가 바뀌면 이것이 달라져 고친 것이 쓰이지 않는다. */
	was: string;
	/** 고친 문구. 비었으면 그 줄을 뺀다. */
	text: string;
}

/** 자막 한 줄(고치기 전). 시각은 편집 화면의 시간(조각을 그냥 이은 시간, 브릿지 없음)이다. */
export interface SubtitleCue {
	start: number;
	end: number;
	at: number | null;
	text: string;
}

/** 다듬는 중인 구간 — 클립(조합 클립이면 첫 조각)의 처음과 (마지막 조각의) 끝, 원본 절대 초. */
export interface ClipTrim {
	start: number;
	end: number;
}

export interface LayoutBox {
	top: number;
	height: number;
}

/** 서버가 계산한 자리. 🔴 화면은 칸 위치를 따로 계산하지 않고 이것을 그린다. */
export interface LayoutGeometry {
	frame: { w: number; h: number };
	safe: { top: number; bottom: number };
	boxes: Partial<Record<"badge" | "headline" | "slot" | "credit", LayoutBox>>;
	slotLimits: [number, number];
	/** 장면마다 자를 영역(px). start 는 클립 안의 초다. */
	crops: { start: number; w: number; h: number; x: number; y: number }[];
	/** 가장 많이 키우는 장면의 배율. */
	upscale: number;
	warnings: string[];
}

export interface TemplateBlock {
	type: "badge" | "headline" | "slot" | "credit";
	[key: string]: unknown;
}

export interface ShortsTemplate {
	id: number;
	name: string;
	kind: "card";
	origin: "seed" | "manual" | "reference";
	spec: { background: string; anchor_y: number; font: string; blocks: TemplateBlock[] };
	/** spec 이 잘못됐으면 null 이고 error 에 이유가 있다. */
	slotLimits: [number, number] | null;
	error: string | null;
}

export interface ShortsSeries {
	id: number;
	name: string;
	template_id: number | null;
	/** 배지 이미지 주소. null 이면 시리즈 이름을 글자 배지로 그린다. */
	badgeUrl: string | null;
}

/** 편집 화면 한 벌(`GET /api/clips/{id}/layout`). */
export interface ClipEditorState {
	clip: {
		id: number;
		title: string | null;
		start_sec: number;
		end_sec: number;
		total_sec: number | null;
		/** 장면 시작 초가 놓이는 시간의 길이(조각 합, 브릿지 제외). */
		duration: number;
		rendered: boolean;
		/** 조합 클립의 조각들. 조각이 하나뿐이면 빈 목록이고 start~end 를 돈다. */
		parts: { ordinal: number; start_sec: number; end_sec: number }[];
		/** 처음 · 끝을 옮길 수 있는 범위(원본 절대 초) — 그 조각의 청크 안. */
		bounds: [number, number];
		renderedAs: "card" | "blur" | null;
		renderedAt: string | null;
	};
	source: {
		id: number;
		width: number;
		height: number;
		channel: string | null;
		seriesId: number | null;
		videoUrl: string;
		/** 원본 프레임률 — 단축키 ←/→ 가 한 프레임씩 옮긴다. 못 읽으면 30. */
		fps: number;
	};
	layout: ClipLayout;
	/** 거짓이면 아직 저장 안 한 초안이다. */
	saved: boolean;
	/** 구운 영상이 지금 명세와 다르다 — 다시 렌더해야 한다. */
	stale: boolean;
	/** 저장된 명세가 지금은 풀리지 않을 때의 이유(템플릿이 지워졌다 등). 그때 layout 은 초안이다. */
	error: string | null;
	geometry: LayoutGeometry;
	/** 이미 그려 둔 글자 층. 없으면 null(보기 전용은 새로 그리지 않는다). */
	cardUrl: string | null;
	templates: ShortsTemplate[];
	series: ShortsSeries[];
}

/** 장면 나누기 결과(`POST /api/clips/{id}/layout/scenes`). 시각은 모두 클립 안의 초다. */
export interface SceneSplit {
	layout: ClipLayout;
	geometry: LayoutGeometry;
	scenes: {
		starts: number[];
		/** 찾은 컷과 점수(앞 프레임과 얼마나 달라졌나, 0~1). */
		cuts: { t: number; score: number }[];
		/** 문턱(0.2) 아래의 애매한 곳. */
		weak: { t: number; score: number }[];
		/** 클립 처음 · 끝에 다른 장면이 잠깐 끼어 있으면 그 길이(초). */
		headFlash: number | null;
		tailFlash: number | null;
		duration: number;
	};
}

/** AI 초점(tease §5-10 "포커스 규칙") — 장면마다 어떻게 맞췄나. */
export type FocusHow = "group" | "speaker" | "one" | "none" | "missing" | "person";

export interface SceneFocus {
	layout: ClipLayout;
	geometry: LayoutGeometry;
	focus: {
		/** 장면마다. group = 모두 담음 · speaker = 둘 중 말하는 사람 · one = 한 명 · none = 사람 없음(그대로) ·
		 * missing = AI 가 그 장면을 답하지 않음(그대로) · person = 사람이 잡은 상자라 보내지 않음. */
		scenes: { scene: number; how: FocusHow; people: number | null }[];
		/** 모델을 불렀나. 모든 장면을 사람이 잡았으면 부르지 않는다. */
		called: boolean;
		latencyMs: number;
	};
}

export interface LayoutPreview {
	layout: ClipLayout;
	geometry: LayoutGeometry;
	cardUrl: string;
}

export type YoutubePrivacy = "private" | "unlisted" | "public";

/** 유튜브 업로드 한 번(clip_uploads). */
export interface ShortsClipUpload {
	id: number;
	/** 실제로 보낸 값 — 대화상자에서 고친 뒤의 것이다. */
	title: string;
	privacy: YoutubePrivacy;
	/** UPLOADING 은 잡이 도는 중. 재기동하면 FAILED 로 정리된다(채널에 올라갔는지 확인해야 한다). */
	status: "UPLOADING" | "DONE" | "FAILED";
	youtube_video_id: string | null;
	channel_id: string | null;
	error: string | null;
	created_at: string;
	finished_at: string | null;
	/** 올라간 영상(올라가지 않았으면 null). 🔴 주소는 서버가 짓는다(upload._view) — 화면이 따로 짓지 않는다. */
	shorts_url: string | null;
	/** 유튜브 스튜디오 편집 화면. 관련 동영상(원본 링크)은 API 로 달 수 없어 여기서 지정한다. */
	studio_url: string | null;
}

/**
 * 연결된 채널. 🔴 refresh token 은 서버가 싣지 않는다.
 * 채널 id·이름은 **첫 업로드 뒤에** 채워진다 — 업로드 권한만으로는 채널 정보를 물을 수 없다.
 */
export interface YoutubeChannel {
	channel_id: string | null;
	channel_title: string | null;
	scopes: string;
	connected_at: string;
	/** 댓글 권한(youtube.force-ssl)을 받았나. 거짓이면 댓글 가져오기·답글이 "다시 연결" 을 안내한다(2026-11-01). */
	can_comment: boolean;
	/** 성과(분석) 권한 둘(yt-analytics.readonly · youtube.readonly)을 받았나(2026-11-01, tease §7-6). */
	can_analytics: boolean;
}

export interface YoutubeState {
	/** .env 에 OAuth 클라이언트 ID·시크릿이 있나. */
	configured: boolean;
	redirectUri: string;
	channel: YoutubeChannel | null;
}

export interface YoutubeUploadDraft {
	title: string;
	description: string;
	privacy: YoutubePrivacy;
}

export interface YoutubeUploadForm {
	defaults: YoutubeUploadDraft;
	uploads: ShortsClipUpload[];
	channel: YoutubeChannel | null;
	configured: boolean;
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
	/** 그 단계를 무엇으로 돌렸나. stt 면 언어·무음 거르기·용어 힌트(initial_prompt), LLM 이면 시도 횟수 같은 것. */
	params: Record<string, unknown> | null;
	created_at: string;
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
	/** 보관한 대사의 발화 수(tease §5-1). 0 이면 그 출처는 아직 없다. 쓰는 대사는 `source.caption_source`. */
	transcripts: Record<TranscriptOrigin, number>;
	stt: {
		/** 음성 인식이 들은 범위. 🔴 같은 오디오는 다시 듣지 않는다. */
		spans: ShortsSttSpan[];
		heardSec: number;
		/** 아직 안 들은 분석 범위. 0 이고 heardSec 가 있으면 음성 인식은 끝났다 — 버튼이 사라진다. */
		remainingSec: number;
	};
}

export type TranscriptOrigin = "youtube" | "whisper";

export interface ShortsSttSpan {
	start_sec: number;
	end_sec: number;
	model: string;
	language: string | null;
	created_at: string;
}

/**
 * 자막과 음성 인식이 다른 곳 하나. 종류는 서버(transcript_diff.KINDS)와 같은 순서로 보여 준다.
 * `invented` = 음성 인식 쪽에 0초 단어가 있다 — 소리 없이 지어냈을 수 있다(뜻을 바꾸는 것이 이것).
 */
export type TranscriptSpotKind = "invented" | "caption_missing" | "whisper_missing" | "different" | "number";

export interface TranscriptSpot {
	start_sec: number;
	end_sec: number;
	kind: TranscriptSpotKind;
	caption: string;
	whisper: string;
	before: string;
	after: string;
}

export type TranscriptDiff =
	| { available: false; reason: string }
	| {
			available: true;
			comparedSec: number;
			captionChars: number;
			whisperChars: number;
			mismatchChars: number;
			mismatchRatio: number;
			byKind: Record<TranscriptSpotKind, number>;
			spots: TranscriptSpot[];
	  };

// ---------------------------------------------------------------- 시청자 질문 묶기

/** 질문 원문 하나. 좋아요는 클러스터가 아니라 질문에 붙어서 재집계에도 보존된다. */
export interface ShortsClusterQuestion {
	id: number;
	text: string;
	created_at: string;
	/**
	 * 어디서 왔나(tease §3-5). `live_chat` 은 끝난 라이브의 채팅 다시보기, `comment` 는 유튜브 댓글에서
	 * 가져온 것이다.
	 */
	origin: "page" | "live_chat" | "comment";
	/** 채팅이 달린 영상 안의 초. 우리 페이지 질문은 null. */
	asked_at_sec: number | null;
	// SQL 이 센 좋아요 수(question_likes 조인). 서버가 계산해 주므로 화면에서 다시 세지 않는다.
	likes: number;
	/** 유튜브 댓글이면 거기 단 가장 최근 답글(tease §7-5). 묶인 질문에만 온다 — 미분류 목록엔 없다. */
	reply?: QuestionReplyState | null;
}

/** 질문 줄에 붙는 답글 상태. 주소는 서버가 짓는다(adapters/youtube.comment_url). */
export interface QuestionReplyState {
	status: "POSTING" | "DONE" | "FAILED";
	url: string | null;
	error: string | null;
}

/** 답글 기록 한 줄(question_replies). */
export interface QuestionReply extends QuestionReplyState {
	id: number;
	text: string;
	created_at: string;
	finished_at: string | null;
}

/** 답글 입력란을 열 때 받는 것. 네트워크 없이 서버가 아는 것만 담는다 — 영상 주인 확인은 보낼 때 한다. */
export interface ReplyDraft {
	question: { id: number; text: string };
	/** 초안 — 숏폼 제목 · 원본 시각 · (비공개가 아니면) Shorts 주소. 사람이 고친 뒤 보낸다. */
	text: string;
	clip: { id: number; title: string | null; parts: { start_sec: number; end_sec: number }[]; shorts_url: string | null } | null;
	/** 누구 이름으로 달리나. */
	channel: YoutubeChannel | null;
	reply: QuestionReply | null;
	/** 못 다는 이유. null 이면 달 수 있다. */
	blocked: string | null;
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
	/**
	 * 겨룬 후보들. **클립보다 먼저 존재한다** — 무엇을 만들지 고르는 것이 크리에이터의 일이다.
	 * 답하기를 돌리지 않았으면 빈 배열이다.
	 */
	candidates: ShortsCandidate[];
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

/**
 * 겨룬 후보 하나. **대사 전문이 들어 있다** — 크리에이터가 이걸 읽고 무엇을 만들지 고른다.
 *
 * 🔴 판정만 보여주면 "조합, 2조각, 28초, 자립 X, 45점" 이 되는데 그걸로는 고를 수가 없다.
 * 판정은 `recommended` 로 추천이 될 뿐이고 결정은 사람이 한다.
 */
export interface ShortsCandidate {
	id: number;
	/** single(직접 답하는 한 덩어리) · combo(흩어진 답을 이어붙임) · tight(핵심만 짧게) */
	label: string | null;
	/** rank 가 왜 이렇게 잘랐는지. */
	reason: string | null;
	parts: ShortsCandidatePart[];
	totalSec: number;
	standalone: boolean | null;
	answers: boolean | null;
	score: number | null;
	/** judge 소견 한두 문장. 왜 자립하지 않는다고 봤는지가 여기 있다. */
	judgeNote: string | null;
	/** 이미 이걸로 만들었는가. run 당 하나만 참이다. */
	chosen: boolean;
	/** 두 관문을 통과한 것 중 최고점. **추천일 뿐 결정이 아니다.** */
	recommended: boolean;
}

export interface ShortsCandidatePart {
	ordinal: number;
	start_sec: number;
	end_sec: number;
	/** 🔴 이 조각의 대사 전문. 이 화면이 존재하는 이유다. */
	text: string;
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

/**
 * 회차 리포트 (`GET /api/sources/{id}/report`, 기획서 §05-③).
 *
 * 🔴 대시보드가 아니라 **산출물**이다. 크리에이터가 다음 설명회 큐시트로 쓰고 공고에서 빠진
 * 정보를 채우는 데 쓴다 — 그래서 화면에서 읽는 것만큼 **복사해 가져갈 수 있는 것**이 중요하다.
 */
export interface ShortsReport {
	source: {
		id: number;
		title: string;
		channel: string | null;
		durationSec: number | null;
		published: boolean;
	};
	summary: {
		questions: number;
		/** [집계] 를 아직 안 눌러 어느 묶음에도 안 붙은 질문. */
		unclustered: number;
		clusters: number;
		answered: number;
		/** 찾아봤는데 이 영상에 없는 것. 리포트의 본체다. */
		missing: number;
		/** 아직 [답하기] 를 안 누른 것. */
		waiting: number;
		/** 만들었는데 크리에이터가 물린 것. */
		declined: number;
	};
	missing: ShortsReportQuestion[];
	waiting: ShortsReportQuestion[];
	declined: ShortsReportQuestion[];
	answered: ShortsReportAnswered[];
	/** 유튜브 감상 댓글과 AI 요약(tease §3-6). 댓글을 한 번도 안 가져왔으면 total 0. */
	reactions: ShortsReactions;
	cost: ShortsReportCost;
}

/** 시청자 반응 — 감상 댓글 원문 전부와 최신 AI 요약. */
export interface ShortsReactions {
	/** 받은 댓글(영상 주인 글 제외). */
	total: number;
	/** 그중 질문 갈래로 간 것. 질문 탭에서 다룬다. */
	questions: number;
	/** 감상 갈래, 오래된 것부터. 요약이 있어도 원문을 전부 보인다. */
	reviews: ShortsReview[];
	fetchedAt: string | null;
	summary: ShortsReactionSummary | null;
}

export interface ShortsReview {
	id: number;
	text: string;
	publishedAt: string | null;
}

export type ShortsReactionKind = "positive" | "concern" | "request";

export interface ShortsReactionTheme {
	kind: ShortsReactionKind;
	/** 좋았던 점 · 아쉬운 점 · 바라는 것 */
	label: string;
	title: string;
	detail: string;
	/** 근거 댓글 id(ShortsReview.id). 🔴 개수는 이 길이다 — 모델이 말한 숫자가 아니다. */
	comments: number[];
	count: number;
}

export interface ShortsReactionSummary {
	id: number;
	overview: string;
	themes: ShortsReactionTheme[];
	/** 요약할 때 본 감상 수. */
	reviews: number;
	createdAt: string;
	/** 요약 뒤 댓글을 다시 가져와 감상 수가 바뀌었다. 자동으로 다시 요약하지 않는다. */
	stale: boolean;
}

export interface ShortsReportQuestion {
	clusterId: number;
	question: string;
	askedBy: number;
	likes: number;
	/** 🔴 왜 답하지 못했는지. 목록만으로는 다음 회차에 무엇을 준비할지 알 수 없다. */
	reason: string | null;
	error: string | null;
	suggestedSourceId: number | null;
	/** 같은 뜻으로 묶인 질문 원문들. 대표 문장은 LLM 이 지은 것이다. */
	texts: string[];
}

export interface ShortsReportAnswered {
	clusterId: number;
	question: string;
	askedBy: number;
	likes: number;
	clipId: number | null;
	totalSec: number | null;
	parts: number;
	published: boolean;
}

export interface ShortsReportCost {
	/** 영상당 한 번 드는 값 — 전사·구간 분할·임베딩. */
	prepare: ShortsCost;
	/** 질문마다 드는 값 — 분류·후보·판정·컷·렌더. */
	answer: ShortsCost;
	/** 🔴 답한 질문이 아니라 **답하기를 돌린 질문** 수. 못 답한 것도 돈을 썼다. */
	attempted: number;
	perQuestionKrw: number | null;
	note: string;
}


// ---------------------------------------------------------------- 프롬프트 (GET /api/prompts)

/** 프롬프트 층. 1층 규칙은 코드라 고칠 수 없고 보기만 한다. */
export type PromptLayerKey = "rule" | "standard" | "reaction" | "context" | "request" | "auto";

export interface PromptLayer {
	key: PromptLayerKey;
	label: string;
	who: string;
	editable: boolean;
	note: string;
}

/** 템플릿 원문 조각. 글이거나 칸이다. 서버가 `string.Formatter` 로 쪼갠 것이라 원문과 정확히 같다. */
export type PromptPart =
	| { kind: "text"; text: string }
	| { kind: "slot"; name: string; spec: string; conversion: string; layer: PromptLayerKey };

export interface PromptStage {
	key: string;
	name: string;
	/** 영상 준비 · 기준 경로 · 질문 경로 */
	path: string;
	module: string;
	why: string;
	/** 🔴 원문에 관리자 기준 칸이 있는가. 따로 적은 표시가 아니라 원문에서 판단한 값이다. */
	usesStandard: boolean;
	/** 원문에 시청자 반응 요약 관점 칸이 있는가. */
	usesReaction: boolean;
	parts: PromptPart[];
}

export interface PromptStandard {
	body: string;
	updatedAt: string | null;
	maxChars: number;
}

export interface PromptCatalog {
	layers: PromptLayer[];
	stages: PromptStage[];
	standard: PromptStandard;
	/** 시청자 반응 요약 관점(tease §3-6). 관리자 기준과 다른 글이다. */
	reaction: PromptStandard;
}


// ---------------------------------------------------------------- 유튜브 성과 (GET /api/sources/{id}/youtube/stats)
//
// 🔴 우리 시청자 퍼널(ShortsInsights · viewer_events)과 **다른 타입**이다. 섞지 않는다 — 시청자 페이지는 없어질 수
// 있다(사용자 결정 2026-11-01, tease §7-6).

/** 숏폼 하나의 마지막으로 가져온 수치. 분석 값이 null 이면 "아직 처리 전" 이다(0 이 아니다). */
export interface YoutubeShortStats {
	/** 유튜브가 돌려줬나. 거짓이면 지웠거나 볼 수 없게 됐다. */
	found: boolean;
	/** 지금 공개 범위. 올릴 때 고른 값과 다를 수 있다. */
	privacy: string | null;
	// 지금 수치 — Data API, 바로 반영
	viewCount: number | null;
	likeCount: number | null;
	commentCount: number | null;
	// 분석 합계 — Analytics, 48~72시간 늦다
	/** 🔴 재생이 시작될 때마다(다시 재생 포함) 센 조회. 2025-03-31 부터의 Shorts 방식. */
	views: number | null;
	/** 첫 프레임을 넘겨 본 조회. 예전 Shorts 조회 방식. */
	engagedViews: number | null;
	minutesWatched: number | null;
	avgViewSec: number | null;
	avgViewPct: number | null;
	likes: number | null;
	comments: number | null;
	shares: number | null;
	subscribersGained: number | null;
	/** 분석이 이 날(태평양)까지다. */
	analyticsThrough: string | null;
	fetchedAt: string;
}

export interface YoutubeShort {
	uploadId: number;
	clipId: number | null;
	title: string;
	uploadedAt: string;
	uploadedPrivacy: string;
	videoId: string;
	shortsUrl: string;
	studioUrl: string;
	/** 지금 연결된 채널이 아닌 곳에 올렸다 — 성과를 읽을 수 없다. */
	otherChannel: boolean;
	/** 한 번도 가져오지 않았으면 null. */
	stats: YoutubeShortStats | null;
	daily: { day: string; views: number | null; engagedViews: number | null }[];
}

export interface YoutubeStatsView {
	shorts: YoutubeShort[];
	/** 찾은 숏폼을 더한 것. 평균(길이·비율)은 더하지 않는다. */
	totals: {
		shorts: number;
		viewCount: number | null;
		likeCount: number | null;
		commentCount: number | null;
		views: number | null;
		engagedViews: number | null;
		minutesWatched: number | null;
		likes: number | null;
		comments: number | null;
		shares: number | null;
		subscribersGained: number | null;
	} | null;
	fetchedAt: string | null;
	through: string | null;
	channel: { connected: boolean; canAnalytics: boolean; channelTitle: string | null };
}

/** AI 카피 후보 하나(tease §5-10 "AI 카피 후보"). 숫자는 서버가 대사와 맞대 본 것만 온다. */
export interface CopyCandidate {
	lines: string[];
	/** 각도 — "결론 먼저" 처럼 짧게. */
	angle: string;
	/** 근거 구절(대사 그대로). 대사에 없었으면 null. */
	quote: string | null;
	/** 글자 수가 넘친 줄의 번호. */
	over: number[];
}

export interface CopySuggestion {
	candidates: CopyCandidate[];
	/** 검증에서 버린 후보 수(대사에 없는 숫자 · 줄 수). */
	dropped: number;
	latencyMs: number;
}
