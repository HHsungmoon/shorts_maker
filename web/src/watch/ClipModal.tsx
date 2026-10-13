import { useMemo, useState } from "react";
import { Modal } from "../shared/Modal";
import { time } from "../shared/format";
import { clipFileUrl, recordEvent } from "./api";
import type { WatchClip, WatchClipPart } from "./api";

/**
 * 조각들이 **이어붙인 영상 안에서** 각각 몇 초에 시작하는지.
 *
 * 🔴 브릿지 카드 길이를 상수로 두지 않고 **영상 길이에서 역산한다.** 조각 길이의 합과 실제
 * 영상 길이의 차이가 곧 브릿지 전체 길이이기 때문이다. 0.4 를 여기 적으면
 * `ffmpeg.render_parts` 의 `bridge_sec` 과 두 곳이 되고, 한쪽만 바뀌면 버튼이 조용히 엉뚱한
 * 조각을 가리킨다 — 예외도 로그도 없이 틀리는 종류라 가장 나쁘다.
 *
 * `duration` 을 아직 모르면(메타데이터 로드 전) 브릿지를 0 으로 본다. 그만큼 경계가 조금
 * 이르게 잡힐 뿐이고, `loadedmetadata` 가 오면 다시 계산된다.
 */
function partOffsets(parts: WatchClipPart[], duration: number): number[] {
	const lengths = parts.map((part) => Math.max(0, part.end_sec - part.start_sec));
	const spoken = lengths.reduce((sum, length) => sum + length, 0);
	const bridge =
		parts.length > 1 && Number.isFinite(duration) && duration > spoken
			? (duration - spoken) / (parts.length - 1)
			: 0;
	let at = 0;
	return lengths.map((length) => {
		const start = at;
		at += length + bridge;
		return start;
	});
}

/**
 * 숏폼을 화면 가운데에서 크게 튼다.
 *
 * 🔴 **목록 안에서 바로 재생하던 것을 여기로 옮겼다**(2026-09-20). 카드가 한 줄에 둘이라
 * 폭이 170px 남짓인데, 9:16 영상을 그 안에서 재생하면 **자막이 읽히지 않는다** — 자막이
 * 이 클립 내용의 절반이라 안 보이면 답을 못 들은 것과 같다. 목록의 카드는 이제 고르는
 * 자리이고, 보는 자리는 여기다.
 *
 * 🔴 **본문은 통째로 영상 몫이다.** 곁다리(수요·원본 이동)는 본문이 아니라 **모달 머리**에
 * 올린다(2026-09-20). 본문에 두었더니 영상이 그만큼 밀려 화면을 넘겼고, 숏폼을 보려고
 * 스크롤을 내려야 했다 — 30초짜리를 한눈에 못 보면 이 화면은 실패한 것이다.
 *
 * 🔴 질문은 모달 머리에만 쓴다. 옆에도 적었더니 같은 문장이 두 번 보였다 — 제목이
 * 대표 문장으로 떨어지기 때문이다(watch.py 의 `coalesce(c.title, qc.canonical_text)`).
 *
 * 🔴 재생·완주 이벤트도 여기서 센다. 목록에 그대로 두면 카드가 화면에 스쳐 지나가는 것까지
 * 재생으로 세어져 퍼널이 부풀었다.
 */
export function ClipModal({
	clip,
	sourceId,
	onClose,
	onJump,
}: {
	clip: WatchClip | null;
	sourceId: number;
	onClose: () => void;
	/** 원본의 몇 초로 보낼지는 **지금 보고 있는 조각**이 정한다(`part` 는 그 조각 번호). */
	onJump: (clip: WatchClip, atSec: number, part: number | null) => void;
}) {
	// 끝까지 본 사람에게는 버튼 문구가 달라진다. ref 가 아니라 state 다 — 버튼이 영상 위에
	// 있어서 다시 그려지지 않으면 문구가 영영 안 바뀐다.
	const [ended, setEnded] = useState(false);
	// 지금 재생 위치와 영상 길이. 조합 클립에서 **CTA 가 따라가야 할 조각**을 고르는 데 쓴다.
	const [at, setAt] = useState(0);
	const [duration, setDuration] = useState(Number.NaN);

	// 🔴 memo 한다 — `?? []` 는 렌더마다 새 배열이라 아래 useMemo 가 매번 다시 돈다.
	const parts = useMemo(() => clip?.parts ?? [], [clip]);
	const offsets = useMemo(() => partOffsets(parts, duration), [parts, duration]);
	// 지금 재생 중인 조각 — 시작 오프셋이 현재 위치보다 앞인 것 중 마지막.
	let index = 0;
	for (let n = 0; n < offsets.length; n += 1) {
		if (offsets[n] <= at) {
			index = n;
		}
	}
	// 🔴 조각이 하나뿐이거나 아예 없으면 예전과 똑같이 동작한다 — `clip.start_sec` 으로 간다.
	const current = parts.length > 1 ? parts[index] : null;
	const target = current ? current.start_sec : (clip?.start_sec ?? 0);

	if (clip === null) {
		return null;
	}

	return (
		<Modal
			open
			variant="modal--clip"
			title={clip.title ?? clip.question ?? "숏폼"}
			onClose={onClose}
			subhead={
				<>
					{clip.liked_by > 0 && (
						<span className="clipmodal__meta">{clip.liked_by}명이 궁금해했어요</span>
					)}
					{/* 🔴 **이 버튼이 이 제품의 결론이다**(tease §2-1 5단계). 숏폼은 답을 주고 끝나는
					    물건이 아니라 원본으로 데려가는 입구다. 모달을 닫고 원본의 그 초로 보낸다.
					    작게, 제목 옆에 둔다 — 본문은 통째로 영상 몫이다. */}
					<button
						type="button"
						className={`clipmodal__jump${ended ? " clipmodal__jump--ready" : ""}`}
						onClick={() => {
							onClose();
							onJump(clip, target, current ? current.ordinal : null);
						}}
					>
						{ended ? "이어 보기" : "원본 보기"} · {time(target)}
					</button>
				</>
			}
		>
			{/* autoPlay — 모달을 연 클릭 자체가 사용자 제스처라 브라우저가 허용한다.
			    막히더라도 controls 가 있으니 누르면 된다. */}
			<video
				key={clip.id}
				className="clipmodal__video"
				src={clipFileUrl(clip.id)}
				controls
				autoPlay
				playsInline
				onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
				// 조합 클립에서만 위치를 따라간다. 조각이 하나면 버튼의 목적지가 바뀌지 않으므로
				// 매 프레임 state 를 건드릴 이유가 없다.
				onTimeUpdate={
					parts.length > 1 ? (event) => setAt(event.currentTarget.currentTime) : undefined
				}
				onPlay={() => recordEvent("short_play", sourceId, { clipId: clip.id })}
				onEnded={() => {
					setEnded(true);
					recordEvent("short_complete", sourceId, { clipId: clip.id });
				}}
			/>
		</Modal>
	);
}
