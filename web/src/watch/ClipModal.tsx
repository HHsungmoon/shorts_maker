import { useState } from "react";
import { Modal } from "../shared/Modal";
import { time } from "../shared/format";
import { clipFileUrl, recordEvent } from "./api";
import type { WatchClip } from "./api";

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
	onJump: (clip: WatchClip) => void;
}) {
	// 끝까지 본 사람에게는 버튼 문구가 달라진다. ref 가 아니라 state 다 — 버튼이 영상 위에
	// 있어서 다시 그려지지 않으면 문구가 영영 안 바뀐다.
	const [ended, setEnded] = useState(false);

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
							onJump(clip);
						}}
					>
						{ended ? "이어 보기" : "원본 보기"} · {time(clip.start_sec)}
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
				onPlay={() => recordEvent("short_play", sourceId, { clipId: clip.id })}
				onEnded={() => {
					setEnded(true);
					recordEvent("short_complete", sourceId, { clipId: clip.id });
				}}
			/>
		</Modal>
	);
}
