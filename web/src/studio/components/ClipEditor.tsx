import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
	createSeries,
	deleteClipLayout,
	deleteSeriesBadge,
	fetchClipLayout,
	fetchClipSubtitles,
	focusClipScenes,
	previewClipLayout,
	renderClip,
	saveClipLayout,
	splitClipScenes,
	uploadSeriesBadge,
} from "../api";
import { useAuth } from "../AuthContext";
import { useAsync } from "../../shared/useAsync";
import type {
	ClipEditorState,
	ClipLayout,
	ClipTrim,
	ClipWindow,
	FocusHow,
	LayoutGeometry,
	SceneFocus,
	SceneSplit,
	ShortsTemplate,
	SubtitleCue,
	SubtitleEdit,
} from "../types";
import type { SourceView } from "../SourcePage";
import "../editor.css";

/**
 * 숏폼 편집 화면(tease §5-10, P1).
 *
 * 왼쪽은 폰 미리보기 — 서버가 그린 글자 층(PNG) 위에 영상 칸만 브라우저가 얹는다. 오른쪽은 원본 전체 화면 위의
 * **영역 상자** 와 설정이다. 상자를 고치면 왼쪽 칸이 바로 따라온다(같은 <video> 를 캔버스로 잘라 그린다).
 *
 * 🔴 칸의 **위치**는 서버가 준 geometry 로만 그린다. 글자 층 PNG 의 글자 자리와 같은 계산에서 나온 값이라야
 * 미리보기에서 맞은 것이 렌더에서도 맞는다. 화면이 계산하는 것은 상자(창)의 모양뿐이고, 저장 · 미리보기 때 서버가
 * 다시 맞춘다(layout.validate).
 *
 * 저장하기 전에는 DB 가 바뀌지 않는다. 미리보기는 저장하지 않은 명세로 그린다.
 *
 * 장면마다 창(2026-11-07, tease §5-10): 재생 막대가 장면 칸으로 나뉜다. 상자는 **지금 재생 위치의 장면**의 것이고,
 * 고치면 그 장면 전체에 든다. 장면 경계의 초기값은 서버가 ffmpeg 장면 점수로 잡는다(처음 여는 초안이면 열 때 한 번).
 * 시간은 모두 **클립 안의 초**다 — 조각을 이어 붙인 시간이고 브릿지는 빠진다(서버 render · scenes 와 같은 규칙).
 *
 * 구간 앞뒤 다듬기 · 자막 고치기(2026-11-08, tease §5-10): 처음 · 끝을 옮기면 저장 전에도 재생 · 장면 · 자막이 새 구간을
 * 따른다(서버 호출에 trim 을 함께 보낸다). 저장하면 명세와 구간이 한 번에 쓰인다. 자막은 문구만 고친다 — 시간은 그대로다.
 */

const FRAME_W = 1080;
const FRAME_H = 1920;
// 서버 layout.MAX_UPSCALE · SOFT_UPSCALE 와 같은 값. 상자를 그보다 좁게 줄이지 못하게 하고, 넘으면 주황으로 알린다.
const MAX_UPSCALE = 3;
const SOFT_UPSCALE = 1.2;
// 서버 layout.MIN_WINDOW_SEC 와 같은 값. 이보다 짧은 장면은 만들지 않는다.
const MIN_SCENE_SEC = 0.2;
// 경계를 끌다가 자동으로 잡은 컷이 이 안에 있으면 거기 붙인다. 컷에서 몇 프레임만 어긋나도 그 프레임들은 옆 장면의
// 상자로 잘린다 — 손으로 프레임을 맞추기는 어렵다.
const SNAP_SEC = 0.25;
// 미리보기 요청을 미루는 시간. 글자를 칠 때마다 ffmpeg 를 돌리지 않는다(같은 명세는 서버가 캐시한다).
const PREVIEW_DELAY_MS = 350;
// 서버 cutting.TRIM_MIN_PART_SEC · TRIM_MAX_CLIP_SEC 와 같은 값. 조각은 1초 이상, 클립은 쇼츠 상한(3분) 이하.
const TRIM_MIN_PART_SEC = 1;
const TRIM_MAX_SEC = 180;
// 서버 layout.SUBTITLE_TEXT_MAX · subtitles.LINE_CHARS 와 같은 값. 한 줄에 16자쯤, 두 줄로 접힌다.
const SUBTITLE_TEXT_MAX = 60;
const SUBTITLE_LINE_CHARS = 16;
// 자막 줄을 다시 읽기 전에 기다리는 시간 — 처음 · 끝을 0.1초씩 여러 번 누를 때 매번 읽지 않는다.
const SUBTITLE_DELAY_MS = 300;
// 처음 · 끝을 옮기는 단추. 0.1초는 단어 하나, 1초는 문장 조각 하나쯤이다.
const TRIM_STEPS: [string, number][] = [
	["−1초", -1],
	["−0.1", -0.1],
	["+0.1", 0.1],
	["+1초", 1],
];
// 자주 쓰는 칸 비율. 칸 높이 옆에 "약 5:4" 처럼 적어 원티드 숏폼과 견줘 보게 한다.
const RATIOS: [string, number][] = [
	["16:9", 607.5],
	["3:2", 720],
	["4:3", 810],
	["5:4", 864],
	["6:5", 900],
	["1:1", 1080],
];

type DragMode = "move" | "nw" | "ne" | "sw" | "se" | "n" | "s";

function clamp(value: number, low: number, high: number): number {
	return Math.min(Math.max(value, low), high);
}

function even(value: number): number {
	return Math.round(value / 2) * 2;
}

function ratioLabel(slot: number): string {
	const hit = RATIOS.find(([, height]) => Math.abs(height - slot) <= 8);
	return hit ? `약 ${hit[0]}` : `1080 × ${slot}`;
}

/** 창의 높이(원본 높이 대비). 서버 layout.window_height 와 같은 식이다. */
function windowHeight(win: ClipWindow, slot: number, srcW: number, srcH: number): number {
	return (win.w * srcW * slot) / FRAME_W / srcH;
}

/**
 * 칸 비율에 맞는 창. 주어진 창의 가운데와 폭을 지키고 원본 밖으로 넘치는 만큼만 줄이고 민다.
 * 서버 layout.fit_window 와 같은 계산이다 — 저장할 때 서버가 다시 맞추므로 어긋나도 저장값은 서버 것이다.
 */
function fitWindow(slot: number, srcW: number, srcH: number, win?: ClipWindow): ClipWindow {
	const ratio = slot / FRAME_W;
	const maxW = Math.min(1, srcH / (srcW * ratio));
	const w = win ? Math.min(win.w, maxW) : maxW;
	const cx = win ? win.x + win.w / 2 : 0.5;
	const cy = win ? win.y + Math.min(windowHeight(win, slot, srcW, srcH), 1) / 2 : 0.5;
	const h = (w * srcW * ratio) / srcH;
	return {
		start: win?.start ?? 0,
		end: win?.end ?? null,
		x: Math.max(Math.min(cx - w / 2, 1 - w), 0),
		y: Math.max(Math.min(cy - h / 2, 1 - h), 0),
		w,
		// 칸 높이 · 템플릿이 바뀌어 다시 맞춘 것은 누가 정했는지를 바꾸지 않는다. 새로 만드는 창(가운데 · 가장 크게)은 사람이다.
		by: win?.by ?? "person",
	};
}

type Range = readonly [number, number];

/** 클립 안의 시간(조각을 이은 시간, 브릿지 제외) ↔ 원본 시각. */
interface Timeline {
	ranges: Range[];
	total: number;
	toSource: (t: number) => number;
	toClip: (s: number) => number;
}

/** 저장된 구간들(원본 절대 초). 서버 render.clip_ranges 와 같은 규칙 — 조각이 둘 이상일 때만 조각이다. */
function clipRanges(clip: ClipEditorState["clip"]): Range[] {
	return clip.parts.length > 0
		? clip.parts.map((p) => [p.start_sec, p.end_sec] as const)
		: [[clip.start_sec, clip.end_sec] as const];
}

/** 다듬은 구간 — 첫 조각의 처음과 마지막 조각의 끝만 바뀐다(서버 cutting.trim_ranges 와 같은 규칙). */
function trimRanges(base: Range[], trim: ClipTrim | null): Range[] {
	if (!trim) {
		return base;
	}
	const out = base.map(([a, b]) => [a, b]);
	out[0][0] = trim.start;
	out[out.length - 1][1] = trim.end;
	return out.map(([a, b]) => [a, b] as const);
}

/**
 * 처음을 offset 초 옮긴 뒤의 장면들. 장면 시작 초는 클립 안의 초라 함께 옮긴다. 새 0초를 덮는 장면이 첫 장면이 되고
 * (0초 뒤 MIN_SCENE_SEC 안에서 시작하는 장면도 첫 장면으로 당긴다 — 그보다 짧은 장면은 없다), 끝 밖으로 나간 장면은 빠진다.
 */
function retime(windows: ClipWindow[], offset: number, total: number): ClipWindow[] {
	const shifted = windows.map((w) => ({ ...w, start: Math.round((w.start - offset) * 1000) / 1000 }));
	let first = 0;
	shifted.forEach((w, i) => {
		if (w.start < MIN_SCENE_SEC) {
			first = i;
		}
	});
	return shifted
		.filter((w, i) => i === first || (i > first && w.start <= total - MIN_SCENE_SEC))
		.map((w, i) => (i === 0 ? { ...w, start: 0 } : w));
}

function makeTimeline(ranges: Range[]): Timeline {
	const total = ranges.reduce((sum, [a, b]) => sum + (b - a), 0);
	return {
		ranges,
		total,
		toSource: (t) => {
			let left = t;
			for (const [a, b] of ranges) {
				if (left <= b - a) {
					return a + Math.max(left, 0);
				}
				left -= b - a;
			}
			return ranges[ranges.length - 1][1];
		},
		toClip: (s) => {
			let before = 0;
			for (const [a, b] of ranges) {
				if (s <= b) {
					return before + Math.max(s - a, 0);
				}
				before += b - a;
			}
			return total;
		},
	};
}

/** 클립 안의 t 초가 속한 장면(창)의 번호. */
function sceneAt(windows: ClipWindow[], t: number): number {
	let index = 0;
	windows.forEach((w, i) => {
		if (w.start <= t + 1e-6) {
			index = i;
		}
	});
	return index;
}

function seconds(t: number): string {
	return `${t.toFixed(1)}초`;
}

/** 원본 시각 "4:18.2" — 구간을 0.1초 단위로 다듬으니 소수 한 자리까지 보인다. */
function clockLabel(sec: number): string {
	const tenths = Math.round(sec * 10);
	const minutes = Math.floor(tenths / 600);
	const rest = (tenths % 600) / 10;
	return `${minutes}:${rest.toFixed(1).padStart(4, "0")}`;
}

/** 이 줄에 붙는 고친 문구. 서버 subtitles.apply_edits 와 같은 열쇠 — 원본 시각과 원래 문구가 둘 다 맞아야 한다. */
function editFor(edits: SubtitleEdit[], cue: SubtitleCue): SubtitleEdit | undefined {
	return cue.at === null ? undefined : edits.find((e) => Math.abs(e.at - (cue.at ?? 0)) <= 0.0005 && e.was === cue.text);
}

/** 두 줄로 접기. 서버 subtitles.wrap 과 같은 규칙 — 가운데에서 가장 가까운 띄어쓰기에서 접는다. */
function wrapSubtitle(text: string): string[] {
	if (text.length <= SUBTITLE_LINE_CHARS) {
		return [text];
	}
	const target = Math.floor(text.length / 2);
	const spaces = [...text].flatMap((ch, i) => (ch === " " ? [i] : []));
	if (spaces.length === 0) {
		return [text.slice(0, target), text.slice(target)];
	}
	const cut = spaces.reduce((best, i) => (Math.abs(i - target) < Math.abs(best - target) ? i : best));
	return [text.slice(0, cut), text.slice(cut + 1)];
}

const BY_LABEL: Record<ClipWindow["by"], string> = { auto: "기본값", ai: "AI", person: "사람이 고침" };

const FOCUS_LABEL: [FocusHow, string][] = [
	["group", "여럿"],
	["speaker", "말하는 사람"],
	["one", "한 명"],
	["none", "사람 없음(그대로)"],
	["missing", "AI 답 없음(그대로)"],
	["person", "사람이 고친 장면(그대로)"],
];

/** AI 초점 결과 한 줄 — "AI 초점 · 여럿 3 · 말하는 사람 1 · 한 명 2 · 17초". */
function focusNote(focus: SceneFocus["focus"]): string {
	if (!focus.called) {
		return "모든 장면을 사람이 고쳐서 AI 를 부르지 않았습니다";
	}
	const parts = FOCUS_LABEL.map(([how, label]) => [label, focus.scenes.filter((s) => s.how === how).length] as const)
		.filter(([, count]) => count > 0)
		.map(([label, count]) => `${label} ${count}`);
	return `AI 초점 · ${parts.join(" · ")} · ${Math.round(focus.latencyMs / 1000)}초`;
}

/** 템플릿의 기본 칸 높이(짝수, 범위 안). 템플릿을 바꾸면 그 템플릿이 정한 모양으로 시작한다. */
function slotDefault(template: ShortsTemplate): number | null {
	const block = template.spec.blocks.find((b) => b.type === "slot");
	if (!block || !template.slotLimits) {
		return null;
	}
	return clamp(even(Number(block.default)), template.slotLimits[0], template.slotLimits[1]);
}

function headlineBlock(template: ShortsTemplate | undefined): { lines: number; maxChars: number } {
	const block = template?.spec.blocks.find((b) => b.type === "headline");
	if (!block) {
		return { lines: 0, maxChars: 0 };
	}
	return { lines: Array.isArray(block.lines) ? block.lines.length : 2, maxChars: Number(block.max_chars ?? 15) };
}

export function ClipEditor({ view }: { view: SourceView }) {
	const { clipId } = useParams();
	const id = Number(clipId);
	const { canAct } = useAuth();
	const { act, submit, jobBusy, reloadToken, sourceId } = view;
	const state = useAsync<ClipEditorState>(() => fetchClipLayout(id), [id, reloadToken], {
		keepPrevious: true,
		subject: id,
	});
	const data = state.data;

	const [draft, setDraft] = useState<ClipLayout | null>(null);
	// 🔴 사람이 고치고 아직 저장하지 않았나. 잡이 끝나며 화면이 다시 읽혀도(reloadToken) 고치던 것을 덮지 않는다.
	const dirtyRef = useRef(false);
	const [dirty, setDirtyState] = useState(false);
	// 원본 무대를 접었나. 화면 상태라 저장하지 않는다.
	const [folded, setFolded] = useState(false);
	// 재생 위치(클립 안의 초). 상자 · 칸 설정 · 장면 막대가 이 시각의 장면을 본다.
	const [clock, setClock] = useState(0);
	// 마지막 장면 나누기의 결과(컷 · 번쩍임). 경계를 끌 때 컷에 붙이는 데 쓴다 — 저장하지 않는다.
	const [sceneInfo, setSceneInfo] = useState<SceneSplit["scenes"] | null>(null);
	const [sceneNote, setSceneNote] = useState<string | null>(null);
	const [trimNote, setTrimNote] = useState<string | null>(null);
	// 다듬는 중인 구간(원본 절대 초). null 이면 저장된 구간 그대로다. 명세와 함께 저장된다.
	const [trim, setTrimState] = useState<ClipTrim | null>(null);
	const trimRef = useRef<ClipTrim | null>(null);
	const setTrim = (value: ClipTrim | null) => {
		trimRef.current = value;
		setTrimState(value);
	};
	const setDirty = (value: boolean) => {
		dirtyRef.current = value;
		setDirtyState(value);
	};
	useEffect(() => {
		if (data && !dirtyRef.current) {
			setDraft(data.layout);
			// 저장하고 다시 읽으면 서버의 구간이 새 구간이다.
			trimRef.current = null;
			setTrimState(null);
		}
	}, [data]);

	// 함수를 받으면 **그때의** 초안에서 고친다 — 끌기처럼 한 렌더 사이에 여러 번 고칠 때 앞의 것을 잃지 않는다.
	const edit = (patch: Partial<ClipLayout> | ((previous: ClipLayout) => Partial<ClipLayout>)) => {
		setDraft((previous) =>
			previous ? { ...previous, ...(typeof patch === "function" ? patch(previous) : patch) } : previous,
		);
		setDirty(true);
	};

	const clipInfo = data?.clip;
	const timeline = useMemo(
		() => (clipInfo ? makeTimeline(trimRanges(clipRanges(clipInfo), trim)) : null),
		[clipInfo, trim],
	);

	// 구간을 옮긴 뒤 보여 줄 자리(클립 안의 초). 🔴 새 구간이 화면에 들어간 **뒤에** 옮긴다 — 재생기는 옛 구간 밖의 시각을
	// 구간 안으로 되돌린다(RegionStage.step). 자식(RegionStage)의 효과가 부모보다 먼저 돌아 그때는 새 구간을 안다.
	const pendingSeek = useRef<number | null>(null);
	const seekClip = useCallback(
		(t: number, line: Timeline) => {
			const at = clamp(t, 0, line.total);
			setClock(at);
			const video = document.getElementById(`sm-edit-video-${id}`) as HTMLVideoElement | null;
			if (video) {
				video.currentTime = line.toSource(at);
			}
		},
		[id],
	);
	useEffect(() => {
		if (pendingSeek.current !== null && timeline) {
			seekClip(pendingSeek.current, timeline);
			pendingSeek.current = null;
		}
	}, [timeline, seekClip]);

	// ------------------------------------------------------------ 자막 줄(고치기 전)
	// 구간이 바뀌면 다시 읽는다. 문구를 고치는 것은 명세(subtitle_edits)라 서버를 부르지 않는다.
	const [cues, setCues] = useState<SubtitleCue[] | null>(null);
	const [cueError, setCueError] = useState<string | null>(null);
	const cueKey = timeline ? JSON.stringify(timeline.ranges) : null;
	useEffect(() => {
		if (!cueKey) {
			return;
		}
		let alive = true;
		const timer = window.setTimeout(() => {
			fetchClipSubtitles(id, trimRef.current)
				.then((next) => {
					if (alive) {
						setCues(next.cues);
						setCueError(null);
					}
				})
				.catch((error: unknown) => {
					if (alive) {
						setCueError(error instanceof Error ? error.message : String(error));
					}
				});
		}, SUBTITLE_DELAY_MS);
		return () => {
			alive = false;
			window.clearTimeout(timer);
		};
	}, [cueKey, id]);

	// **지금의** 초안. 미루어 둔 미리보기 요청과 늦게 오는 AI 초점 응답이 보낼 때 · 받을 때의 최신 명세를 읽게 한다.
	const draftRef = useRef(draft);
	useEffect(() => {
		draftRef.current = draft;
	}, [draft]);

	// AI 초점 — 사람이 잡지 않은 장면마다 사람을 찾아 상자를 맞춘다(Gemini 1회, 클립 4 에서 17초).
	// 🔴 기다리는 동안 사람이 고친 것을 덮지 않는다. 응답이 오면 **그때의** 초안에서, 장면 경계가 보낸 때와 같고 그 사이
	// 사람이 잡지 않은 창만 바꾼다. 경계가 바뀌었으면 통째로 버린다 — 장면 번호가 어긋나 엉뚱한 장면에 상자가 간다.
	const [focusing, setFocusing] = useState(false);
	const focusScenes = useCallback(
		async (layout: ClipLayout, srcW: number, srcH: number) => {
			setFocusing(true);
			setSceneNote("AI 가 장면마다 사람을 찾고 있습니다 — 10~20초 걸립니다");
			try {
				const next = await focusClipScenes(id, layout, trimRef.current);
				const current = draftRef.current;
				const same =
					current !== null &&
					current.windows.length === next.layout.windows.length &&
					current.windows.every((w, i) => Math.abs(w.start - next.layout.windows[i].start) < 0.001);
				if (!current || !same) {
					setSceneNote("AI 가 찾는 동안 장면 경계가 바뀌어 결과를 버렸습니다 — [AI 초점] 을 다시 누르세요");
					return;
				}
				// 칸 높이를 그 사이 바꿨으면 지금 칸 비율로 다시 맞춘다(가운데와 폭을 지킨다).
				const windows = current.windows.map((w, i) =>
					w.by === "person"
						? w
						: fitWindow(current.slot_height, srcW, srcH, { ...next.layout.windows[i], start: w.start, end: w.end }),
				);
				setDraft({ ...current, windows });
				// 유료로 받은 결과다 — 화면을 다시 읽어도(reloadToken) 날아가지 않게 "고친 것" 으로 둔다.
				setDirty(true);
				setSceneNote(focusNote(next.focus));
			} catch (error: unknown) {
				setSceneNote(
					`AI 초점을 맞추지 못했습니다 — ${error instanceof Error ? error.message : String(error)} (상자는 그대로입니다)`,
				);
			} finally {
				setFocusing(false);
			}
		},
		[id],
	);

	// 처음 여는 초안이면 열 때 한 번 장면을 나누고 AI 초점을 맞춘다. 🔴 열기(GET)는 ffmpeg · 모델을 돌리지 않는다 —
	// 나누기 · 초점은 따로 POST 다. 저장한 명세(장면 하나인 예전 것 포함)는 사람이 [장면 나누기] 를 누른다 — 사람이
	// 합친 것을 다시 쪼개지 않고, 열 때마다 유료 호출이 나가지 않는다.
	const autoSplit = useRef<number | null>(null);
	useEffect(() => {
		if (!data || !draft || data.saved || !canAct || draft.windows.length !== 1 || autoSplit.current === id) {
			return;
		}
		autoSplit.current = id;
		splitClipScenes(id, draft, trimRef.current)
			.then((next) => {
				setSceneInfo(next.scenes);
				// 기다리는 동안 사람이 고치기 시작했으면 덮지 않는다(AI 초점도 부르지 않는다).
				if (!dirtyRef.current) {
					setDraft(next.layout);
					draftRef.current = next.layout;
					void focusScenes(next.layout, data.source.width, data.source.height);
				}
			})
			.catch((error: unknown) => {
				setSceneNote(`장면을 나누지 못했습니다 — ${error instanceof Error ? error.message : String(error)}`);
			});
	}, [data, draft, canAct, id, focusScenes]);

	// ------------------------------------------------------------ 미리보기(글자 층 + 자리)
	const [preview, setPreview] = useState<{ cardUrl: string | null; geometry: LayoutGeometry } | null>(null);
	const [previewError, setPreviewError] = useState<string | null>(null);
	const [previewing, setPreviewing] = useState(false);
	const seq = useRef(0);
	const selectedSeries = data?.series.find((s) => s.id === draft?.series_id) ?? null;
	// 글자 층을 바꾸는 값만 열쇠에 넣는다. 상자(창)는 글자 층과 무관해서 끄는 동안 서버를 부르지 않는다.
	const previewKey = draft
		? JSON.stringify([
				draft.template_id,
				draft.series_id,
				draft.headline,
				draft.credit,
				draft.slot_height,
				selectedSeries?.badgeUrl ?? null,
				selectedSeries?.name ?? null,
			])
		: null;
	useEffect(() => {
		if (!previewKey || !canAct || !draftRef.current) {
			return;
		}
		const mine = ++seq.current;
		setPreviewing(true);
		const timer = window.setTimeout(() => {
			const current = draftRef.current;
			if (!current) {
				return;
			}
			previewClipLayout(id, current, trimRef.current)
				.then((next) => {
					// 늦게 온 옛 응답이 최신 미리보기를 덮지 않게 한다.
					if (mine === seq.current) {
						setPreview({ cardUrl: next.cardUrl, geometry: next.geometry });
						setPreviewError(null);
					}
				})
				.catch((error: unknown) => {
					if (mine === seq.current) {
						setPreviewError(error instanceof Error ? error.message : String(error));
					}
				})
				.finally(() => {
					if (mine === seq.current) {
						setPreviewing(false);
					}
				});
		}, PREVIEW_DELAY_MS);
		return () => window.clearTimeout(timer);
	}, [previewKey, canAct, id]);

	if (state.error && !data) {
		return (
			<div className="sm-edit">
				<p className="state state--error">편집 화면을 열지 못했습니다. ({state.error.message})</p>
				<p className="sm-meta">
					<Link to={`/studio/sources/${sourceId}/clips`}>← 완성 클립으로</Link>
				</p>
			</div>
		);
	}
	if (!data || !draft) {
		return <p className="state">불러오는 중…</p>;
	}

	const template = data.templates.find((t) => t.id === draft.template_id);
	const limits: [number, number] = template?.slotLimits ?? data.geometry.slotLimits;
	// 새 미리보기가 아직 없으면 서버가 열 때 준 것(이미 그려 둔 PNG · 자리). 보기 전용은 이것뿐이다.
	const geometry = preview?.geometry ?? data.geometry;
	const cardUrl = preview?.cardUrl ?? data.cardUrl;
	const srcW = data.source.width;
	const srcH = data.source.height;
	const line = timeline ?? makeTimeline(clipRanges(data.clip));
	const scenes = draft.windows;
	const scene = sceneAt(scenes, clock);
	const win = scenes[scene];
	const sceneEnd = scenes[scene + 1]?.start ?? line.total;
	const upscale = FRAME_W / (win.w * srcW);
	const headline = headlineBlock(template);

	// 템플릿을 바꾸면 그 템플릿의 기본 칸 높이로, 창은 가운데를 지킨 채 새 칸 비율에 맞춘다.
	const pickTemplate = (templateId: number, patch: Partial<ClipLayout> = {}) => {
		const next = data.templates.find((t) => t.id === templateId);
		const slot = next ? (slotDefault(next) ?? draft.slot_height) : draft.slot_height;
		edit({
			...patch,
			template_id: templateId,
			slot_height: slot,
			windows: draft.windows.map((w) => fitWindow(slot, srcW, srcH, w)),
		});
	};

	// 시리즈를 바꾸면 그 시리즈의 템플릿으로 함께 바꾼다(시리즈 = 배지 + 기본 템플릿).
	const pickSeries = (seriesId: number | null) => {
		const templateId = data.series.find((s) => s.id === seriesId)?.template_id;
		const usable = data.templates.find((t) => t.id === templateId && t.error === null);
		if (usable && usable.id !== draft.template_id) {
			pickTemplate(usable.id, { series_id: seriesId });
			return;
		}
		edit({ series_id: seriesId });
	};

	const setSlot = (value: number) => {
		const slot = clamp(even(value), limits[0], limits[1]);
		// 칸 높이는 클립에 하나다 — 모든 장면의 창이 새 비율로 함께 맞춰진다.
		edit({ slot_height: slot, windows: draft.windows.map((w) => fitWindow(slot, srcW, srcH, w)) });
	};

	// ------------------------------------------------------------ 장면
	// 장면 index 의 창을 바꾼다. 칸 높이까지 바뀌면(위·아래 변) 다른 장면의 창도 새 비율로 맞춘다.
	const changeWindow = (index: number, next: ClipWindow, slot?: number) =>
		edit((previous) => {
			const windows = previous.windows.map((w, i) =>
				i === index
					? { ...next, start: w.start, end: w.end }
					: slot === undefined
						? w
						: fitWindow(slot, srcW, srcH, w),
			);
			return slot === undefined ? { windows } : { windows, slot_height: slot };
		});

	const splitHere = () => {
		if (clock - win.start < MIN_SCENE_SEC || sceneEnd - clock < MIN_SCENE_SEC) {
			setSceneNote(`장면 경계에서 ${MIN_SCENE_SEC}초 안에서는 나눌 수 없습니다 — 재생 위치를 옮기고 누르세요`);
			return;
		}
		setSceneNote(null);
		const at = Math.round(clock * 1000) / 1000;
		edit((previous) => {
			const windows = [...previous.windows];
			// 새 장면은 지금 장면의 상자로 시작한다 — 나누기만 해서는 화면이 바뀌지 않는다.
			windows.splice(scene + 1, 0, { ...previous.windows[scene], start: at });
			return { windows };
		});
	};

	// 지금 장면의 시작 경계를 지운다 — 앞 장면이 늘어나고 그 상자를 쓴다.
	const mergeWithPrevious = () => {
		setSceneNote(null);
		edit((previous) => ({ windows: previous.windows.filter((_, i) => i !== scene) }));
	};

	const sameBoxEverywhere = () => {
		setSceneNote(null);
		edit((previous) => ({
			windows: previous.windows.map((w) => ({ ...w, x: win.x, y: win.y, w: win.w, by: "person" as const })),
		}));
	};

	const moveBoundary = (index: number, t: number) =>
		edit((previous) => {
			const low = previous.windows[index - 1].start + MIN_SCENE_SEC;
			const high = (previous.windows[index + 1]?.start ?? line.total) - MIN_SCENE_SEC;
			const snapped = sceneInfo?.cuts.find((cut) => Math.abs(cut.t - t) <= SNAP_SEC)?.t ?? t;
			const start = Math.round(clamp(snapped, low, high) * 1000) / 1000;
			return { windows: previous.windows.map((w, i) => (i === index ? { ...w, start } : w)) };
		});

	// ------------------------------------------------------------ 구간 앞뒤 다듬기
	const base = clipRanges(data.clip);
	const ranges = line.ranges;
	const first = ranges[0];
	const last = ranges[ranges.length - 1];
	const lastFrom = line.total - (last[1] - last[0]); // 마지막 조각이 시작하는 클립 안의 초

	// 처음을 start, 끝을 end 로(원본 절대 초). 청크 안 · 조각 1초 이상 · 180초 이하로 맞추고, 장면 시작 초를 함께 옮긴다.
	// show 는 옮긴 뒤 보여 줄 쪽 — 처음을 옮겼으면 첫 프레임, 끝을 옮겼으면 끝 2초 앞(재생을 누르면 끝이 들린다).
	const changeTrim = (start: number, end: number, show: "start" | "end") => {
		const [low, high] = data.clip.bounds;
		const single = ranges.length === 1;
		const s0 = Math.round(clamp(start, low, (single ? end : first[1]) - TRIM_MIN_PART_SEC) * 1000) / 1000;
		const e0 = Math.round(clamp(end, (single ? s0 : last[0]) + TRIM_MIN_PART_SEC, high) * 1000) / 1000;
		const next = trimRanges(base, { start: s0, end: e0 });
		const total = next.reduce((sum, [a, b]) => sum + (b - a), 0);
		if (total > TRIM_MAX_SEC + 1e-6) {
			setTrimNote(`쇼츠는 ${TRIM_MAX_SEC}초까지입니다 — 더 늘릴 수 없습니다`);
			return;
		}
		const offset = s0 - first[0];
		const grown = e0 - last[1];
		if (Math.abs(offset) < 0.0005 && Math.abs(grown) < 0.0005) {
			setTrimNote(
				s0 <= low + 0.0005 || e0 >= high - 0.0005
					? "대사를 받은 범위의 끝입니다 — 더 늘릴 수 없습니다"
					: "조각은 1초보다 짧아질 수 없습니다",
			);
			return;
		}
		setTrimNote(null);
		edit((previous) => ({ windows: retime(previous.windows, offset, total) }));
		const saved = Math.abs(s0 - base[0][0]) < 0.0005 && Math.abs(e0 - base[base.length - 1][1]) < 0.0005;
		setTrim(saved ? null : { start: s0, end: e0 });
		// 장면 나누기가 찾은 컷도 같이 옮긴다(경계를 끌 때 붙는 자리). 처음 · 끝에 낀 장면의 길이도 옮긴 만큼 줄거나 는다.
		setSceneInfo((info) => {
			if (!info) {
				return info;
			}
			const head = info.headFlash === null ? null : info.headFlash - offset;
			const tail = info.tailFlash === null ? null : info.tailFlash + grown;
			return {
				...info,
				cuts: info.cuts.map((cut) => ({ ...cut, t: cut.t - offset })),
				headFlash: head !== null && head > 0.01 ? head : null,
				tailFlash: tail !== null && tail > 0.01 ? tail : null,
			};
		});
		pendingSeek.current = show === "start" ? 0 : Math.max(total - 2, 0);
	};

	const nudgeStart = (delta: number) => changeTrim(first[0] + delta, last[1], "start");
	const nudgeEnd = (delta: number) => changeTrim(first[0], last[1] + delta, "end");
	const startHere = () => {
		if (clock > first[1] - first[0]) {
			setTrimNote("재생 위치가 첫 조각 밖입니다 — 처음은 첫 조각 안에서만 옮깁니다");
			return;
		}
		changeTrim(line.toSource(clock), last[1], "start");
	};
	const endHere = () => {
		if (clock < lastFrom) {
			setTrimNote("재생 위치가 마지막 조각 밖입니다 — 끝은 마지막 조각 안에서만 옮깁니다");
			return;
		}
		changeTrim(first[0], line.toSource(clock), "end");
	};

	// ------------------------------------------------------------ 자막 고치기
	// 문구만 고친다 — 줄의 시간은 서버가 대사에서 만든 그대로다. 원래 문구로 되돌리면 고친 것에서 뺀다.
	const setCueText = (cue: SubtitleCue, text: string) => {
		const at = cue.at;
		if (at === null) {
			return;
		}
		edit((previous) => {
			const rest = previous.subtitle_edits.filter((e) => !(Math.abs(e.at - at) <= 0.0005 && e.was === cue.text));
			return { subtitle_edits: text === cue.text ? rest : [...rest, { at, was: cue.text, text }] };
		});
	};
	const cueNow = cues?.find((cue) => clock >= cue.start && clock < cue.end) ?? null;
	const subtitleNow = cueNow ? (editFor(draft.subtitle_edits, cueNow)?.text ?? cueNow.text) : null;
	// 구간 밖이거나 대사가 바뀌어 맞는 줄이 없는 고친 것. 지우지 않는다 — 구간을 되돌리면 다시 붙는다.
	const unusedEdits = cues ? draft.subtitle_edits.filter((e) => !cues.some((cue) => editFor([e], cue))).length : 0;
	const slotBase = template ? slotDefault(template) : null;
	const subtitleSize = Number(template?.spec.blocks.find((b) => b.type === "slot")?.subtitle_size ?? 58);

	// 장면을 (다시) 나누고 AI 초점을 맞춘다. 새 장면은 그 장면 가운데를 덮던 상자를 물려받는다. 사람이 고친 상자는
	// 새 장면과 1:1 일 때만 사람 것으로 남고(서버 layout.split), 나머지는 AI 가 다시 맞춘다.
	const resplit = () =>
		act(async () => {
			const next = await splitClipScenes(id, draft, trimRef.current);
			setSceneInfo(next.scenes);
			setSceneNote(null);
			setDraft(next.layout);
			draftRef.current = next.layout;
			setDirty(true);
			void focusScenes(next.layout, srcW, srcH);
		});

	const refocus = () =>
		act(async () => {
			if (!focusing) {
				await focusScenes(draft, srcW, srcH);
			}
		});

	// 구간은 그대로 둔다 — 다시 읽을 때(act 가 reloadToken 을 올린다) 서버의 새 구간과 함께 비워진다. 지금 비우면 다시
	// 읽기 전까지 옛 구간 위에 새 장면 시각이 놓인다.
	const save = () =>
		act(async () => {
			const next = await saveClipLayout(id, draft, trim);
			setDirty(false);
			setDraft(next.layout);
		});

	const saveAndRender = () =>
		act(async () => {
			if (dirty || !data.saved) {
				const next = await saveClipLayout(id, draft, trim);
				setDirty(false);
				setDraft(next.layout);
			}
			// 잡이다 — 진행은 위 배너가, 결과는 아래 "구운 영상" 이 말한다.
			submit(() => renderClip(id, true));
		});

	const dropCard = () =>
		act(async () => {
			await deleteClipLayout(id);
			setDirty(false);
			setPreview(null);
		});

	return (
		<div className="sm-edit">
			<div className="sm-edit__head">
				<Link className="sm-edit__back" to={`/studio/sources/${sourceId}/clips`}>
					← 완성 클립
				</Link>
				<h2 className="sm-edit__title">{data.clip.title ?? `클립 ${data.clip.id}`}</h2>
				<span className="sm-edit__status">
					{!data.saved && !dirty && <span className="sm-edit__chip">처음 여는 초안</span>}
					{dirty && <span className="sm-edit__chip sm-edit__chip--warn">저장 안 됨</span>}
					{data.saved && !dirty && data.stale && (
						<span className="sm-edit__chip sm-edit__chip--warn">렌더한 영상과 다름</span>
					)}
					{data.saved && !dirty && !data.stale && <span className="sm-edit__chip sm-edit__chip--ok">렌더와 같음</span>}
				</span>
				<span className="sm-actions sm-actions--end">
					<button type="button" className="button button--small" onClick={save}>
						저장
					</button>
					<button type="button" className="button button--small sm-go" disabled={jobBusy} onClick={saveAndRender}>
						저장하고 렌더
					</button>
				</span>
			</div>

			{data.error && <p className="sm-edit__notice">저장된 명세를 열지 못해 초안을 보여 줍니다 — {data.error}</p>}
			{!canAct && (
				<p className="sm-edit__notice">
					보기 전용입니다. 고친 내용은 저장되지 않고, 미리보기도 새로 그리지 않습니다(그려 둔 것만 보입니다).
				</p>
			)}

			<div className="sm-edit__grid">
				{/* 무대(입력)와 폰(결과)을 한 줄에. 폭을 원본 비율 : 9/16 로 나눠 둘의 세로 길이를 맞춘다(--src-ar).
				    접으면 무대가 반으로 줄고 폰이 커진다 — 상자는 대강 잡고 결과를 크게 볼 때. */}
				<div
					className={`sm-edit__view${folded ? " sm-edit__view--folded" : ""}`}
					style={{ "--src-ar": data.source.width / data.source.height } as CSSProperties}
				>
					<div className="sm-edit__main">
						<RegionStage
							data={data}
							draft={draft}
							limits={limits}
							timeline={line}
							clock={clock}
							onClock={setClock}
							scene={scene}
							onWindow={changeWindow}
							onBoundary={moveBoundary}
						/>
						{/* 구간 — 원본 어디부터 어디까지인가. 처음 · 끝을 0.1초 · 1초씩 옮기거나 재생 위치로 자른다. */}
						<div className="sm-edit__trim">
							<span className="sm-edit__trimlabel">
								구간 {clockLabel(first[0])}–{clockLabel(last[1])}
								{ranges.length > 1 ? ` · 조각 ${ranges.length}개` : ""} · {seconds(line.total)}
								{trim && <span className="sm-edit__chip sm-edit__chip--warn">다듬음</span>}
							</span>
							<span className="sm-edit__trimgroup">
								<span className="sm-meta">처음</span>
								{TRIM_STEPS.map(([label, delta]) => (
									<button key={label} type="button" className="button button--small sm-edit__step" onClick={() => nudgeStart(delta)}>
										{label}
									</button>
								))}
								<button type="button" className="button button--small" onClick={startHere} title="재생 위치를 클립의 처음으로">
									여기부터
								</button>
							</span>
							<span className="sm-edit__trimgroup">
								<span className="sm-meta">끝</span>
								{TRIM_STEPS.map(([label, delta]) => (
									<button key={label} type="button" className="button button--small sm-edit__step" onClick={() => nudgeEnd(delta)}>
										{label}
									</button>
								))}
								<button type="button" className="button button--small" onClick={endHere} title="재생 위치를 클립의 끝으로">
									여기까지
								</button>
							</span>
							{trim && (
								<button
									type="button"
									className="button button--small"
									onClick={() => changeTrim(base[0][0], base[base.length - 1][1], "start")}
								>
									저장된 구간으로
								</button>
							)}
						</div>
						{trimNote && <p className="sm-meta sm-edit__scenenote">{trimNote}</p>}
						<div className="sm-edit__scenetools">
							<span className="sm-edit__scenelabel">
								장면 {scene + 1}/{scenes.length} · {seconds(win.start)}–{seconds(sceneEnd)}
							</span>
							<button type="button" className="button button--small" onClick={splitHere}>
								여기서 나누기
							</button>
							{scene > 0 && (
								<button type="button" className="button button--small" onClick={mergeWithPrevious}>
									앞 장면과 합치기
								</button>
							)}
							{scenes.length > 1 && (
								<button type="button" className="button button--small" onClick={sameBoxEverywhere}>
									모든 장면에 이 상자
								</button>
							)}
							<button type="button" className="button button--small" onClick={resplit}>
								{scenes.length > 1 ? "장면 다시 나누기" : "장면 나누기"}
							</button>
							<button
								type="button"
								className="button button--small"
								onClick={refocus}
								title="사람이 고치지 않은 장면마다 사람을 찾아 상자를 맞춥니다 — 여럿이면 모두, 둘이면 말하는 사람, 한 명이면 그 사람(Gemini 1회)"
							>
								{focusing ? "AI 찾는 중…" : "AI 초점"}
							</button>
						</div>
						{sceneNote && <p className="sm-meta sm-edit__scenenote">{sceneNote}</p>}
						{sceneInfo && (sceneInfo.headFlash !== null || sceneInfo.tailFlash !== null) && (
							<p className="sm-meta sm-edit__scenenote">
								{sceneInfo.headFlash !== null && `클립 처음 ${sceneInfo.headFlash.toFixed(2)}초는 앞 장면입니다. `}
								{sceneInfo.tailFlash !== null && `클립 끝 ${sceneInfo.tailFlash.toFixed(2)}초에 다음 장면이 잠깐 보입니다. `}
								{sceneInfo.headFlash !== null && (
									<button
										type="button"
										className="button button--small"
										onClick={() => changeTrim(first[0] + (sceneInfo.headFlash ?? 0), last[1], "start")}
									>
										처음을 장면 경계로
									</button>
								)}
								{sceneInfo.tailFlash !== null && (
									<button
										type="button"
										className="button button--small"
										onClick={() => changeTrim(first[0], last[1] - (sceneInfo.tailFlash ?? 0), "end")}
									>
										끝을 장면 경계로
									</button>
								)}
							</p>
						)}
					</div>
					<button
						type="button"
						className="sm-edit__fold"
						title={folded ? "원본 영상 펼치기" : "원본 영상 접고 결과 크게 보기"}
						aria-label={folded ? "원본 영상 펼치기" : "원본 영상 접기"}
						onClick={() => setFolded((value) => !value)}
					>
						{folded ? "›" : "‹"}
					</button>
					<Phone
						cardUrl={cardUrl}
						geometry={geometry}
						draft={draft}
						background={template?.spec.background ?? "#FFFFFF"}
						previewing={previewing && canAct}
						videoSource={data}
						timeline={line}
						subtitle={draft.subtitles ? subtitleNow : null}
						subtitleSize={subtitleSize}
						font={template?.spec.font ?? "sans-serif"}
					/>
				</div>

				{/* 설정은 무대 아래가 아니라 한 단을 따로 쓴다 — 아래에 두면 상자를 끌면서 칸 높이 · 카피를 볼 수 없다(2026-11-06).
				    자리(왼쪽 설정 · 가운데 폰 · 오른쪽 무대)는 editor.css 의 grid-template-areas 가 정한다. */}
				<div className="sm-edit__form">
					<section className="sm-edit__group">
						<h3>모양</h3>
						<label className="sm-edit__field">
							<span>템플릿</span>
							<select
								className="field__input"
								value={draft.template_id}
								onChange={(event) => pickTemplate(Number(event.target.value))}
							>
								{data.templates.map((t) => (
									<option key={t.id} value={t.id} disabled={t.error !== null}>
										{t.name}
										{t.error ? " (잘못된 템플릿)" : ""}
									</option>
								))}
							</select>
						</label>
						<SeriesField data={data} draft={draft} onSeries={pickSeries} act={act} />
					</section>

					<section className="sm-edit__group">
						<h3>카피 · 출처</h3>
						{Array.from({ length: headline.lines }, (_, index) => {
							const value = draft.headline[index] ?? "";
							const over = headline.maxChars > 0 && value.replace(/\s/g, "").length > headline.maxChars;
							return (
								<label className="sm-edit__field" key={index}>
									<span>
										카피 {index + 1}줄
										<em className={`sm-edit__count${over ? " sm-edit__count--over" : ""}`}>
											{value.replace(/\s/g, "").length}/{headline.maxChars}자
										</em>
									</span>
									<input
										className="field__input"
										value={value}
										maxLength={40}
										placeholder={index === 0 ? "첫 줄(검은 글자)" : "둘째 줄(검은 띠)"}
										onChange={(event) => {
											const lines = Array.from({ length: headline.lines }, (_, i) => draft.headline[i] ?? "");
											lines[index] = event.target.value;
											edit({ headline: lines });
										}}
									/>
								</label>
							);
						})}
						<label className="sm-edit__field">
							<span>출처</span>
							<input
								className="field__input"
								value={draft.credit}
								maxLength={60}
								placeholder="출처 : 채널 원티드"
								onChange={(event) => edit({ credit: event.target.value })}
							/>
						</label>
					</section>

					<section className="sm-edit__group">
						<h3>영상 칸</h3>
						<label className="sm-edit__field">
							<span>
								칸 높이 <em className="sm-edit__count">{draft.slot_height}px · {ratioLabel(draft.slot_height)}</em>
							</span>
							<span className="sm-edit__slider">
								<input
									type="range"
									min={limits[0]}
									max={limits[1]}
									step={2}
									value={draft.slot_height}
									onChange={(event) => setSlot(Number(event.target.value))}
								/>
								{/* 템플릿 기본 칸 높이 자리. 끌어 보다가 처음 모양으로 돌아올 길이다. */}
								{slotBase !== null && limits[1] > limits[0] && (
									<button
										type="button"
										className={`sm-edit__mark${draft.slot_height === slotBase ? " sm-edit__mark--on" : ""}`}
										style={{ "--at": (slotBase - limits[0]) / (limits[1] - limits[0]) } as CSSProperties}
										title={`템플릿 기본 칸 높이 ${slotBase}px — 누르면 돌아갑니다`}
										onClick={() => setSlot(slotBase)}
									>
										기본 {slotBase}
									</button>
								)}
							</span>
							<span className="sm-meta">
								{limits[0]}–{limits[1]}. 위·아래 글자가 유튜브 화면에 가리지 않는 범위입니다. 상자의 위·아래 변을 끌어도 바뀝니다.
							</span>
						</label>
						<div className="sm-edit__row">
							<span className={`sm-edit__scale${upscale > SOFT_UPSCALE ? " sm-edit__scale--warn" : ""}`}>
								{scenes.length > 1 ? `장면 ${scene + 1}: ` : ""}원본 {Math.round(win.w * srcW)}px 폭 → 1080 ·{" "}
								{upscale <= 1 ? "확대 없음" : `${upscale.toFixed(2)}배 확대`}
							</span>
							<button
								type="button"
								className="button button--small"
								onClick={() => changeWindow(scene, fitWindow(draft.slot_height, srcW, srcH))}
							>
								가운데 · 가장 크게
							</button>
						</div>
					</section>

					<section className="sm-edit__group">
						<h3>자막</h3>
						<label className="sm-edit__check">
							<input
								type="checkbox"
								checked={draft.subtitles}
								onChange={(event) => edit({ subtitles: event.target.checked, subtitles_by: "person" })}
							/>
							<span>
								우리 자막 넣기
								<em className="sm-meta"> — 원본 화면에 자막이 박혀 있으면 끕니다. 자막은 칸 안 아래쪽에 들어갑니다.</em>
							</span>
						</label>
						{draft.subtitles &&
							(cueError ? (
								<p className="sm-meta">자막 줄을 읽지 못했습니다 — {cueError}</p>
							) : cues === null ? (
								<p className="sm-meta">자막 줄을 읽는 중…</p>
							) : cues.length === 0 ? (
								<p className="sm-meta">이 구간에는 대사가 없습니다.</p>
							) : (
								<>
									<p className="sm-meta">
										문구만 고칩니다 — 시간은 그대로입니다. 비우면 그 줄을 뺍니다. 시각을 누르면 그 자리로 갑니다.
									</p>
									<ol className="sm-edit__cues">
										{cues.map((cue) => {
											const fixed = editFor(draft.subtitle_edits, cue);
											const value = fixed?.text ?? cue.text;
											const on = cue === cueNow;
											return (
												<li
													key={`${cue.at ?? "b"}-${cue.start}`}
													className={`sm-edit__cue${on ? " sm-edit__cue--on" : ""}${fixed ? " sm-edit__cue--fixed" : ""}`}
												>
													<button
														type="button"
														className="sm-edit__cuetime"
														onClick={() => seekClip(cue.start + 0.01, line)}
													>
														{seconds(cue.start)}
													</button>
													<input
														className="field__input"
														value={value}
														maxLength={SUBTITLE_TEXT_MAX}
														disabled={cue.at === null}
														placeholder="(이 줄 빼기)"
														onChange={(event) => setCueText(cue, event.target.value)}
													/>
													{fixed && (
														<button
															type="button"
															className="sm-edit__cueundo"
															title={`원래 문구로 — ${cue.text}`}
															aria-label="원래 문구로"
															onClick={() => setCueText(cue, cue.text)}
														>
															↺
														</button>
													)}
												</li>
											);
										})}
									</ol>
									{unusedEdits > 0 && (
										<p className="sm-meta">
											고친 줄 {unusedEdits}개는 지금 구간에 맞는 줄이 없어 쓰이지 않습니다(구간을 되돌리면 다시 붙습니다).
										</p>
									)}
								</>
							))}
					</section>

					{(previewError || geometry.warnings.length > 0) && (
						<ul className="sm-edit__warnings">
							{previewError && <li className="sm-edit__warning--error">{previewError}</li>}
							{geometry.warnings.map((warning) => (
								<li key={warning}>{warning}</li>
							))}
						</ul>
					)}

					{data.saved && (
						<section className="sm-edit__group">
							<h3>카드 지우기</h3>
							<p className="sm-meta">
								저장한 카드 설정을 지우고, 다음 렌더부터 예전 방식(위아래 블러 · 자막만)으로 만듭니다. 이미 구운
								영상은 다시 렌더할 때까지 그대로입니다.
							</p>
							<div className="sm-edit__row">
								<button type="button" className="button button--small" onClick={dropCard}>
									카드 지우고 예전 방식으로
								</button>
							</div>
						</section>
					)}

				</div>
			</div>
		</div>
	);
}

/** 시리즈 고르기 · 새로 만들기 · 배지 이미지. */
function SeriesField({
	data,
	draft,
	onSeries,
	act,
}: {
	data: ClipEditorState;
	draft: ClipLayout;
	onSeries: (seriesId: number | null) => void;
	act: (run: () => Promise<unknown>) => void;
}) {
	const [naming, setNaming] = useState<string | null>(null);
	const fileRef = useRef<HTMLInputElement>(null);
	const series = data.series.find((s) => s.id === draft.series_id) ?? null;

	return (
		<>
			<label className="sm-edit__field">
				<span>시리즈</span>
				<span className="sm-edit__row">
					<select
						className="field__input"
						value={draft.series_id ?? ""}
						onChange={(event) => onSeries(event.target.value ? Number(event.target.value) : null)}
					>
						<option value="">시리즈 없음(배지 빔)</option>
						{data.series.map((s) => (
							<option key={s.id} value={s.id}>
								{s.name}
							</option>
						))}
					</select>
					<button type="button" className="button button--small" onClick={() => setNaming("")}>
						새 시리즈
					</button>
				</span>
			</label>
			{naming !== null && (
				<form
					className="sm-edit__row"
					onSubmit={(event) => {
						event.preventDefault();
						const name = naming.trim();
						if (!name) {
							return;
						}
						act(async () => {
							const created = await createSeries(name, draft.template_id);
							setNaming(null);
							// 새 시리즈의 템플릿은 지금 템플릿이다 — 템플릿을 바꿀 일이 없다.
							onSeries(created.id);
						});
					}}
				>
					<input
						className="field__input"
						autoFocus
						maxLength={40}
						value={naming}
						placeholder="시리즈 이름(예: 잡소리)"
						onChange={(event) => setNaming(event.target.value)}
					/>
					<button type="submit" className="button button--small sm-go">
						만들기
					</button>
					<button type="button" className="button button--small" onClick={() => setNaming(null)}>
						취소
					</button>
				</form>
			)}
			{series && (
				<div className="sm-edit__badge">
					{series.badgeUrl ? (
						<img src={series.badgeUrl} alt={`${series.name} 배지`} />
					) : (
						<span className="sm-meta">배지 이미지가 없어 시리즈 이름을 글자로 그립니다.</span>
					)}
					<span className="sm-actions sm-actions--end">
						<button type="button" className="button button--small" onClick={() => fileRef.current?.click()}>
							{series.badgeUrl ? "배지 바꾸기" : "배지 이미지 올리기"}
						</button>
						{series.badgeUrl && (
							<button type="button" className="button button--small" onClick={() => act(() => deleteSeriesBadge(series.id))}>
								이미지 빼기
							</button>
						)}
					</span>
					<input
						ref={fileRef}
						type="file"
						hidden
						accept="image/png,image/jpeg,image/webp"
						onChange={(event) => {
							const file = event.target.files?.[0];
							event.target.value = "";
							if (file) {
								act(() => uploadSeriesBadge(series.id, file));
							}
						}}
					/>
				</div>
			)}
		</>
	);
}

/**
 * 폰 미리보기. 글자 층 PNG 를 깔고, 칸 자리에 캔버스를 얹어 원본에서 창만큼 잘라 그린다.
 *
 * 🔴 캔버스의 영상은 오른쪽 영역 편집기의 <video> 를 그대로 읽는다(id 로 찾는다). 영상을 둘 틀면 원본을 두 번
 * 디코딩하고 두 화면의 시각이 어긋난다.
 */
function Phone({
	cardUrl,
	geometry,
	draft,
	background,
	previewing,
	videoSource,
	timeline,
	subtitle,
	subtitleSize,
	font,
}: {
	cardUrl: string | null;
	geometry: LayoutGeometry;
	draft: ClipLayout;
	background: string;
	previewing: boolean;
	videoSource: ClipEditorState;
	timeline: Timeline;
	/** 지금 시각의 자막(고친 문구). 없거나 자막을 껐으면 null. */
	subtitle: string | null;
	subtitleSize: number;
	font: string;
}) {
	const canvasRef = useRef<HTMLCanvasElement>(null);
	// 그리기 고리는 한 번만 돌리고, 창 · 칸 높이는 이 ref 로 읽는다(바뀔 때마다 고리를 새로 세우지 않는다).
	// 🔴 장면은 **영상의 지금 시각**으로 고른다(React 상태의 재생 위치는 초당 열 번쯤만 바뀐다) — 그래야 칸이 원본의
	// 컷과 같은 프레임에 바뀐다.
	const crop = useRef({ windows: draft.windows, slot: draft.slot_height, timeline });
	useEffect(() => {
		crop.current = { windows: draft.windows, slot: draft.slot_height, timeline };
	}, [draft, timeline]);
	const videoId = `sm-edit-video-${videoSource.clip.id}`;

	useEffect(() => {
		let frame = 0;
		const draw = () => {
			const video = document.getElementById(videoId) as HTMLVideoElement | null;
			const canvas = canvasRef.current;
			if (video && canvas && video.readyState >= 2 && video.videoWidth > 0) {
				const { windows, slot, timeline: line } = crop.current;
				const win = windows[sceneAt(windows, line.toClip(video.currentTime))];
				const sw = win.w * video.videoWidth;
				const sh = (sw * slot) / FRAME_W;
				canvas.getContext("2d")?.drawImage(
					video,
					win.x * video.videoWidth,
					win.y * video.videoHeight,
					sw,
					Math.min(sh, video.videoHeight),
					0,
					0,
					canvas.width,
					canvas.height,
				);
			}
			frame = requestAnimationFrame(draw);
		};
		frame = requestAnimationFrame(draw);
		return () => cancelAnimationFrame(frame);
	}, [videoId]);

	const slot = geometry.boxes.slot;
	// 칸 높이만 바뀌면 칸의 **가운데는 그대로**다(덩어리 가운데가 고정이라 위끝이 올라간 만큼 아래끝이 내려간다).
	// 그래서 새 미리보기가 오기 전에도 서버가 준 가운데에 지금 높이로 그리면 자리가 맞다.
	const middle = slot ? slot.top + slot.height / 2 : FRAME_H / 2;
	const top = middle - draft.slot_height / 2;
	const pct = (value: number) => `${(value / FRAME_H) * 100}%`;

	return (
		<div className="sm-edit__phonewrap">
			<div className="sm-edit__phone" style={{ background }}>
				{cardUrl ? (
					<img className="sm-edit__card" src={cardUrl} alt="글자 층 미리보기" />
				) : (
					<span className="sm-edit__nocard">글자 층 미리보기가 아직 없습니다</span>
				)}
				<canvas
					ref={canvasRef}
					className="sm-edit__slot"
					width={540}
					height={Math.round((540 * draft.slot_height) / FRAME_W)}
					style={{ top: pct(top), height: pct(draft.slot_height) }}
				/>
				{/* 🔴 브라우저가 그린 근사다(tease §5-10 "자막 고치기") — 문구와 자리를 보는 용도이고 글꼴 · 줄바꿈의 정답은 렌더다.
				    크기는 1080 폭 기준 px 을 폰 폭(cqw)으로 줄인다. */}
				{subtitle && (
					<span
						className="sm-edit__subtitle"
						style={{
							bottom: pct(FRAME_H - (top + draft.slot_height) + 36),
							fontSize: `calc(${subtitleSize} / ${FRAME_W} * 100cqw)`,
							fontFamily: `"${font}", sans-serif`,
						}}
					>
						{wrapSubtitle(subtitle).map((text, index) => (
							<span key={index}>{text}</span>
						))}
					</span>
				)}
				<span className="sm-edit__safe" style={{ top: 0, height: pct(geometry.safe.top) }} />
				<span className="sm-edit__safe" style={{ top: pct(geometry.safe.bottom), bottom: 0 }} />
				{previewing && <span className="sm-edit__busy">갱신 중…</span>}
			</div>
			<p className="sm-meta sm-edit__phonenote">빗금은 유튜브 화면(재생 버튼 · 제목 · 설명)이 덮는 자리입니다.</p>
		</div>
	);
}

/**
 * 원본 전체 화면 위의 영역 상자. 안쪽을 끌면 옮기고, 모서리는 비율을 지킨 채 크기를, 위·아래 변은 **칸 높이**를 바꾼다.
 * 상자는 지금 재생 위치의 **장면**의 것이고, 고치면 그 장면 전체에 든다. 재생기는 클립 구간만 돈다(조합 클립이면 조각 순서대로).
 */
function RegionStage({
	data,
	draft,
	limits,
	timeline,
	clock,
	onClock,
	scene,
	onWindow,
	onBoundary,
}: {
	data: ClipEditorState;
	draft: ClipLayout;
	limits: [number, number];
	timeline: Timeline;
	clock: number;
	onClock: (t: number) => void;
	scene: number;
	onWindow: (index: number, next: ClipWindow, slot?: number) => void;
	onBoundary: (index: number, t: number) => void;
}) {
	const stageRef = useRef<HTMLDivElement>(null);
	const videoRef = useRef<HTMLVideoElement>(null);
	const drag = useRef<{
		mode: DragMode;
		index: number;
		x: number;
		y: number;
		win: ClipWindow;
		slot: number;
		rect: DOMRect;
	} | null>(null);
	const [playing, setPlaying] = useState(false);
	const srcW = data.source.width;
	const srcH = data.source.height;
	const win = draft.windows[scene];
	const h = windowHeight(win, draft.slot_height, srcW, srcH);
	const minW = FRAME_W / (MAX_UPSCALE * srcW);

	// 재생 고리는 ref 로 최신 값을 읽는다 — 고리를 장면 · 초안이 바뀔 때마다 다시 세우지 않는다.
	const follow = useRef({ windows: draft.windows, onClock, timeline });
	useEffect(() => {
		follow.current = { windows: draft.windows, onClock, timeline };
	}, [draft.windows, onClock, timeline]);
	const reported = useRef(-1);

	// 영상의 지금 시각 → 클립 안의 초. 구간 밖이면 다음 조각으로(마지막이면 처음으로) 넘긴다.
	// 위로 알리는 것은 0.1초마다 또는 장면이 바뀔 때만 — 매 프레임 알리면 편집 화면 전체가 초당 60번 다시 그려진다.
	const step = useCallback((video: HTMLVideoElement, force = false) => {
		const { windows, onClock: report, timeline: line } = follow.current;
		const now = video.currentTime;
		const inside = line.ranges.findIndex(([a, b]) => now >= a - 0.3 && now < b - 0.05);
		if (inside === -1) {
			const next = line.ranges.findIndex(([a]) => a > now);
			video.currentTime = next === -1 ? line.ranges[0][0] : line.ranges[next][0];
			return;
		}
		const t = line.toClip(now);
		const last = reported.current;
		if (force || Math.abs(t - last) >= 0.1 || sceneAt(windows, t) !== sceneAt(windows, last)) {
			reported.current = t;
			report(t);
		}
	}, []);

	// 🔴 재생 중에는 프레임마다 시각을 읽는다. timeupdate 는 초당 네 번뿐이라 컷 뒤 상자가 0.25초 늦게 따라온다.
	useEffect(() => {
		if (!playing) {
			return;
		}
		let frame = 0;
		const tick = () => {
			if (videoRef.current) {
				step(videoRef.current);
			}
			frame = requestAnimationFrame(tick);
		};
		frame = requestAnimationFrame(tick);
		return () => cancelAnimationFrame(frame);
	}, [playing, step]);

	const seek = (t: number) => {
		const clamped = clamp(t, 0, timeline.total);
		reported.current = clamped;
		onClock(clamped);
		if (videoRef.current) {
			videoRef.current.currentTime = timeline.toSource(clamped);
		}
	};

	const begin = (mode: DragMode) => (event: ReactPointerEvent) => {
		const rect = stageRef.current?.getBoundingClientRect();
		if (!rect) {
			return;
		}
		event.preventDefault();
		event.stopPropagation();
		(event.target as Element).setPointerCapture(event.pointerId);
		// 끄는 동안 재생이 다음 장면으로 넘어가도 **누른 장면**의 상자를 고친다.
		drag.current = { mode, index: scene, x: event.clientX, y: event.clientY, win, slot: draft.slot_height, rect };
	};

	const move = (event: ReactPointerEvent) => {
		const d = drag.current;
		if (!d) {
			return;
		}
		const dx = (event.clientX - d.x) / d.rect.width;
		const dy = (event.clientY - d.y) / d.rect.height;
		const start = d.win;
		const k = (srcW * d.slot) / FRAME_W / srcH; // 창 높이 = 폭 × k
		const h0 = start.w * k;
		if (d.mode === "move") {
			onWindow(d.index, {
				...start,
				x: clamp(start.x + dx, 0, 1 - start.w),
				y: clamp(start.y + dy, 0, 1 - h0),
				by: "person",
			});
			return;
		}
		if (d.mode === "n" || d.mode === "s") {
			// 위·아래 변 = 칸 높이. 폭은 그대로 두고 높이만 바꾼다 — 칸이 길어지면 같은 폭에서 위아래를 더 담는다.
			const top = start.y;
			const bottom = start.y + h0;
			const room = d.mode === "s" ? 1 - top : bottom;
			const wanted = d.mode === "s" ? h0 + dy : h0 - dy;
			const toSlot = (height: number) => (height * srcH * FRAME_W) / (start.w * srcW);
			let slot = even(clamp(toSlot(Math.min(wanted, room)), limits[0], limits[1]));
			if (toSlot(room) < slot) {
				slot = Math.floor(toSlot(room) / 2) * 2;
			}
			if (slot < limits[0]) {
				return; // 이 폭으로는 원본 안에 더 짧은 칸을 못 만든다 — 모서리로 폭을 먼저 줄인다.
			}
			const height = (start.w * srcW * slot) / FRAME_W / srcH;
			onWindow(d.index, { ...start, y: d.mode === "s" ? top : bottom - height, by: "person" }, slot);
			return;
		}
		// 모서리 = 비율을 지킨 크기. 맞은편 모서리를 고정한다.
		const west = d.mode.includes("w");
		const north = d.mode.includes("n");
		const ax = west ? start.x + start.w : start.x;
		const ay = north ? start.y + h0 : start.y;
		const fromX = start.w + (west ? -dx : dx);
		const fromY = (h0 + (north ? -dy : dy)) / k;
		let w = Math.abs(fromX - start.w) >= Math.abs(fromY - start.w) ? fromX : fromY;
		const maxW = Math.min(west ? ax : 1 - ax, (north ? ay : 1 - ay) / k, 1);
		w = clamp(w, Math.min(minW, maxW), maxW);
		onWindow(d.index, { ...start, w, x: west ? ax - w : ax, y: north ? ay - w * k : ay, by: "person" });
	};

	const end = () => {
		drag.current = null;
	};

	const toggle = () => {
		const video = videoRef.current;
		if (!video) {
			return;
		}
		if (video.paused) {
			void video.play();
		} else {
			video.pause();
		}
	};

	return (
		<div className="sm-edit__region">
			<div
				ref={stageRef}
				className="sm-edit__stage"
				style={{ aspectRatio: `${srcW} / ${srcH}` }}
				onPointerMove={move}
				onPointerUp={end}
				onPointerCancel={end}
			>
				<video
					ref={videoRef}
					id={`sm-edit-video-${data.clip.id}`}
					className="sm-edit__video"
					src={data.source.videoUrl}
					preload="auto"
					playsInline
					onLoadedMetadata={() => {
						if (videoRef.current) {
							videoRef.current.currentTime = timeline.toSource(clock);
						}
					}}
					onTimeUpdate={(event) => step(event.currentTarget)}
					onPlay={() => setPlaying(true)}
					onPause={() => setPlaying(false)}
				/>
				<div
					className={`sm-edit__box${FRAME_W / (win.w * srcW) > SOFT_UPSCALE ? " sm-edit__box--soft" : ""}`}
					style={{ left: `${win.x * 100}%`, top: `${win.y * 100}%`, width: `${win.w * 100}%`, height: `${h * 100}%` }}
					onPointerDown={begin("move")}
				>
					{(["nw", "ne", "sw", "se"] as const).map((corner) => (
						<span key={corner} className={`sm-edit__handle sm-edit__handle--${corner}`} onPointerDown={begin(corner)} />
					))}
					{(["n", "s"] as const).map((edge) => (
						<span
							key={edge}
							className={`sm-edit__edge sm-edit__edge--${edge}`}
							title="칸 높이"
							onPointerDown={begin(edge)}
						/>
					))}
					<span className="sm-edit__boxlabel">
						{draft.windows.length > 1 ? `장면 ${scene + 1} · ` : ""}
						{BY_LABEL[win.by]}
					</span>
				</div>
			</div>
			<div className="sm-edit__transport">
				<button type="button" className="button button--small" onClick={toggle}>
					{playing ? "멈춤" : "재생"}
				</button>
				<SceneBar windows={draft.windows} total={timeline.total} clock={clock} scene={scene} onSeek={seek} onBoundary={onBoundary} />
				<span className="sm-meta sm-edit__clock">
					{seconds(clock)} / {seconds(timeline.total)}
				</span>
			</div>
		</div>
	);
}

/**
 * 재생 막대 = 장면 칸. 누르거나 끌면 그 시각으로 가고, 칸 사이의 막대(경계)를 끌면 경계가 옮겨진다.
 * 경계를 끄는 동안 영상도 그 시각을 보여 준다 — 컷에 맞았는지 눈으로 본다.
 */
function SceneBar({
	windows,
	total,
	clock,
	scene,
	onSeek,
	onBoundary,
}: {
	windows: ClipWindow[];
	total: number;
	clock: number;
	scene: number;
	onSeek: (t: number) => void;
	onBoundary: (index: number, t: number) => void;
}) {
	const barRef = useRef<HTMLDivElement>(null);
	const drag = useRef<{ boundary: number | null } | null>(null);
	const at = (clientX: number): number => {
		const rect = barRef.current?.getBoundingClientRect();
		return rect ? clamp((clientX - rect.left) / rect.width, 0, 1) * total : 0;
	};
	const press = (boundary: number | null) => (event: ReactPointerEvent) => {
		event.preventDefault();
		event.stopPropagation();
		barRef.current?.setPointerCapture(event.pointerId);
		drag.current = { boundary };
		if (boundary === null) {
			onSeek(at(event.clientX));
		}
	};
	const move = (event: ReactPointerEvent) => {
		const d = drag.current;
		if (!d) {
			return;
		}
		const t = at(event.clientX);
		if (d.boundary !== null) {
			onBoundary(d.boundary, t);
		}
		onSeek(t);
	};
	const release = () => {
		drag.current = null;
	};
	const pct = (t: number) => `${(t / total) * 100}%`;

	return (
		<div
			ref={barRef}
			className="sm-edit__scenes"
			role="slider"
			aria-label="클립 안의 시각"
			aria-valuemin={0}
			aria-valuemax={total}
			aria-valuenow={clock}
			onPointerDown={press(null)}
			onPointerMove={move}
			onPointerUp={release}
			onPointerCancel={release}
		>
			{windows.map((w, i) => {
				const end = windows[i + 1]?.start ?? total;
				return (
					<span
						key={`scene-${i}`}
						className={`sm-edit__scene${i === scene ? " sm-edit__scene--on" : ""}`}
						style={{ left: pct(w.start), width: pct(end - w.start) }}
						title={`장면 ${i + 1} · ${seconds(w.start)}–${seconds(end)} · ${BY_LABEL[w.by]}`}
					>
						{i + 1}
					</span>
				);
			})}
			{windows.slice(1).map((w, i) => (
				<span
					key={`cut-${i}`}
					className="sm-edit__cut"
					style={{ left: pct(w.start) }}
					title={`장면 ${i + 2} 시작 ${seconds(w.start)} — 끌어서 옮기기`}
					onPointerDown={press(i + 1)}
				/>
			))}
			<span className="sm-edit__playhead" style={{ left: pct(clock) }} />
		</div>
	);
}
