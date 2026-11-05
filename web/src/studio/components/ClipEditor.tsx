import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
	clipUrl,
	createSeries,
	deleteClipLayout,
	deleteSeriesBadge,
	fetchClipLayout,
	previewClipLayout,
	renderClip,
	saveClipLayout,
	uploadSeriesBadge,
} from "../api";
import { useAuth } from "../AuthContext";
import { useAsync } from "../../shared/useAsync";
import { time } from "../../shared/format";
import type { ClipEditorState, ClipLayout, ClipWindow, LayoutGeometry, ShortsTemplate } from "../types";
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
 */

const FRAME_W = 1080;
const FRAME_H = 1920;
// 서버 layout.MAX_UPSCALE · SOFT_UPSCALE 와 같은 값. 상자를 그보다 좁게 줄이지 못하게 하고, 넘으면 주황으로 알린다.
const MAX_UPSCALE = 3;
const SOFT_UPSCALE = 1.2;
// 미리보기 요청을 미루는 시간. 글자를 칠 때마다 ffmpeg 를 돌리지 않는다(같은 명세는 서버가 캐시한다).
const PREVIEW_DELAY_MS = 350;
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
		by: "person",
	};
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
	const setDirty = (value: boolean) => {
		dirtyRef.current = value;
		setDirtyState(value);
	};
	useEffect(() => {
		if (data && !dirtyRef.current) {
			setDraft(data.layout);
		}
	}, [data]);

	const edit = (patch: Partial<ClipLayout>) => {
		setDraft((previous) => (previous ? { ...previous, ...patch } : previous));
		setDirty(true);
	};

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
	// 미루어 둔 미리보기 요청이 **보낼 때의** 최신 명세를 읽게 한다(열쇠가 같아도 창은 바뀌었을 수 있다).
	const draftRef = useRef(draft);
	useEffect(() => {
		draftRef.current = draft;
	}, [draft]);

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
			previewClipLayout(id, current)
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
	const win = draft.windows[0];
	const upscale = FRAME_W / (win.w * srcW);
	const headline = headlineBlock(template);

	// 템플릿을 바꾸면 그 템플릿의 기본 칸 높이로, 창은 가운데를 지킨 채 새 칸 비율에 맞춘다.
	const pickTemplate = (templateId: number, patch: Partial<ClipLayout> = {}) => {
		const next = data.templates.find((t) => t.id === templateId);
		const slot = next ? (slotDefault(next) ?? draft.slot_height) : draft.slot_height;
		edit({ ...patch, template_id: templateId, slot_height: slot, windows: [fitWindow(slot, srcW, srcH, win)] });
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
		edit({ slot_height: slot, windows: [fitWindow(slot, srcW, srcH, win)] });
	};

	const save = () =>
		act(async () => {
			const next = await saveClipLayout(id, draft);
			setDirty(false);
			setDraft(next.layout);
		});

	const saveAndRender = () =>
		act(async () => {
			if (dirty || !data.saved) {
				const next = await saveClipLayout(id, draft);
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
				<h2 className="sm-edit__title" title={data.clip.title ?? undefined}>
					{data.clip.title ?? `클립 ${data.clip.id}`}
				</h2>
				<span className="sm-meta">
					{Math.round(data.clip.total_sec ?? data.clip.end_sec - data.clip.start_sec)}초 · {time(data.clip.start_sec)}
					{data.clip.parts.length > 1 ? " 외 이어붙임" : `–${time(data.clip.end_sec)}`}
				</span>
				<span className="sm-edit__status">
					{!data.saved && !dirty && <span className="sm-edit__chip">처음 여는 초안</span>}
					{dirty && <span className="sm-edit__chip sm-edit__chip--warn">저장 안 됨</span>}
					{data.saved && !dirty && data.stale && (
						<span className="sm-edit__chip sm-edit__chip--warn">렌더한 영상과 다름</span>
					)}
					{data.saved && !dirty && !data.stale && <span className="sm-edit__chip sm-edit__chip--ok">렌더와 같음</span>}
				</span>
				<span className="sm-actions sm-actions--end">
					{data.saved && (
						<button type="button" className="button button--small" onClick={dropCard}>
							카드 쓰지 않기
						</button>
					)}
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
				<Phone
					cardUrl={cardUrl}
					geometry={geometry}
					draft={draft}
					background={template?.spec.background ?? "#FFFFFF"}
					previewing={previewing && canAct}
					videoSource={data}
				/>

				<div className="sm-edit__main">
					<RegionStage
						data={data}
						draft={draft}
						limits={limits}
						onWindow={(next, slot) => edit(slot === undefined ? { windows: [next] } : { windows: [next], slot_height: slot })}
					/>

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
								<input
									type="range"
									min={limits[0]}
									max={limits[1]}
									step={2}
									value={draft.slot_height}
									onChange={(event) => setSlot(Number(event.target.value))}
								/>
								<span className="sm-meta">
									{limits[0]}–{limits[1]}. 위·아래 글자가 유튜브 화면에 가리지 않는 범위입니다. 상자의 위·아래 변을 끌어도 바뀝니다.
								</span>
							</label>
							<div className="sm-edit__row">
								<span className={`sm-edit__scale${upscale > SOFT_UPSCALE ? " sm-edit__scale--warn" : ""}`}>
									원본 {Math.round(win.w * srcW)}px 폭 → 1080 · {upscale <= 1 ? "확대 없음" : `${upscale.toFixed(2)}배 확대`}
								</span>
								<button
									type="button"
									className="button button--small"
									onClick={() => edit({ windows: [fitWindow(draft.slot_height, srcW, srcH)] })}
								>
									가운데 · 가장 크게
								</button>
							</div>
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
						</section>

						{(previewError || geometry.warnings.length > 0) && (
							<ul className="sm-edit__warnings">
								{previewError && <li className="sm-edit__warning--error">{previewError}</li>}
								{geometry.warnings.map((warning) => (
									<li key={warning}>{warning}</li>
								))}
							</ul>
						)}

						{data.clip.renderedAs && (
							<section className="sm-edit__group sm-edit__result">
								<h3>
									구운 영상 <em className="sm-meta">({data.clip.renderedAs === "card" ? "카드" : "예전 블러"})</em>
								</h3>
								<video controls preload="metadata" src={clipUrl(data.clip.id, data.clip.renderedAt)} />
							</section>
						)}
					</div>
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
}: {
	cardUrl: string | null;
	geometry: LayoutGeometry;
	draft: ClipLayout;
	background: string;
	previewing: boolean;
	videoSource: ClipEditorState;
}) {
	const canvasRef = useRef<HTMLCanvasElement>(null);
	// 그리기 고리는 한 번만 돌리고, 창 · 칸 높이는 이 ref 로 읽는다(바뀔 때마다 고리를 새로 세우지 않는다).
	const crop = useRef({ win: draft.windows[0], slot: draft.slot_height });
	useEffect(() => {
		crop.current = { win: draft.windows[0], slot: draft.slot_height };
	}, [draft]);
	const videoId = `sm-edit-video-${videoSource.clip.id}`;

	useEffect(() => {
		let frame = 0;
		const draw = () => {
			const video = document.getElementById(videoId) as HTMLVideoElement | null;
			const canvas = canvasRef.current;
			if (video && canvas && video.readyState >= 2 && video.videoWidth > 0) {
				const { win, slot } = crop.current;
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
				{draft.subtitles && (
					<span className="sm-edit__subtitle" style={{ bottom: pct(FRAME_H - (top + draft.slot_height) + 36) }}>
						우리 자막 자리
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
 * 재생기는 클립 구간만 돈다(조합 클립이면 조각 순서대로).
 */
function RegionStage({
	data,
	draft,
	limits,
	onWindow,
}: {
	data: ClipEditorState;
	draft: ClipLayout;
	limits: [number, number];
	onWindow: (next: ClipWindow, slot?: number) => void;
}) {
	const stageRef = useRef<HTMLDivElement>(null);
	const videoRef = useRef<HTMLVideoElement>(null);
	const drag = useRef<{ mode: DragMode; x: number; y: number; win: ClipWindow; slot: number; rect: DOMRect } | null>(null);
	const [playing, setPlaying] = useState(false);
	const [clock, setClock] = useState(0);
	const srcW = data.source.width;
	const srcH = data.source.height;
	const win = draft.windows[0];
	const h = windowHeight(win, draft.slot_height, srcW, srcH);
	const minW = FRAME_W / (MAX_UPSCALE * srcW);

	// 클립이 원본에서 차지하는 구간들. 조각이 없으면 start~end 하나다.
	const ranges = useMemo(
		() =>
			data.clip.parts.length > 0
				? data.clip.parts.map((p) => [p.start_sec, p.end_sec] as const)
				: [[data.clip.start_sec, data.clip.end_sec] as const],
		[data.clip],
	);
	const total = ranges.reduce((sum, [a, b]) => sum + (b - a), 0);
	const toSource = (t: number): number => {
		let left = t;
		for (const [a, b] of ranges) {
			if (left <= b - a) {
				return a + left;
			}
			left -= b - a;
		}
		return ranges[ranges.length - 1][1];
	};
	const toClip = (s: number): number => {
		let before = 0;
		for (const [a, b] of ranges) {
			if (s >= a && s <= b) {
				return before + (s - a);
			}
			before += b - a;
		}
		return 0;
	};

	const onTimeUpdate = () => {
		const video = videoRef.current;
		if (!video) {
			return;
		}
		const now = video.currentTime;
		const index = ranges.findIndex(([a, b]) => now >= a - 0.3 && now < b - 0.05);
		if (index === -1) {
			// 구간 끝에 닿았다 — 다음 조각으로, 마지막이면 처음으로 돈다.
			const next = ranges.findIndex(([a]) => a > now);
			video.currentTime = next === -1 ? ranges[0][0] : ranges[next][0];
			return;
		}
		setClock(toClip(now));
	};

	const begin = (mode: DragMode) => (event: ReactPointerEvent) => {
		const rect = stageRef.current?.getBoundingClientRect();
		if (!rect) {
			return;
		}
		event.preventDefault();
		event.stopPropagation();
		(event.target as Element).setPointerCapture(event.pointerId);
		drag.current = { mode, x: event.clientX, y: event.clientY, win, slot: draft.slot_height, rect };
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
			onWindow({ ...start, x: clamp(start.x + dx, 0, 1 - start.w), y: clamp(start.y + dy, 0, 1 - h0), by: "person" });
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
			onWindow({ ...start, y: d.mode === "s" ? top : bottom - height, by: "person" }, slot);
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
		onWindow({ ...start, w, x: west ? ax - w : ax, y: north ? ay - w * k : ay, by: "person" });
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
							videoRef.current.currentTime = ranges[0][0];
						}
					}}
					onTimeUpdate={onTimeUpdate}
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
					<span className="sm-edit__boxlabel">{win.by === "person" ? "사람이 고침" : "기본값"}</span>
				</div>
			</div>
			<div className="sm-edit__transport">
				<button type="button" className="button button--small" onClick={toggle}>
					{playing ? "멈춤" : "재생"}
				</button>
				<input
					type="range"
					min={0}
					max={total}
					step={0.1}
					value={clock}
					aria-label="클립 안의 시각"
					onChange={(event) => {
						const t = Number(event.target.value);
						setClock(t);
						if (videoRef.current) {
							videoRef.current.currentTime = toSource(t);
						}
					}}
				/>
				<span className="sm-meta">
					{time(clock)} / {time(total)}
				</span>
			</div>
		</div>
	);
}
