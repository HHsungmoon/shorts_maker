import { useRef } from "react";
import { Modal } from "../shared/Modal";
import { time } from "../shared/format";
import { clipFileUrl, recordEvent } from "./api";
import type { WatchClip } from "./api";

/**
 * 숏폼을 화면 가운데에서 크게 튼다.
 *
 * 🔴 **목록 안에서 바로 재생하던 것을 여기로 옮겼다**(2026-09-20). 카드가 한 줄에 둘이라
 * 폭이 170px 남짓인데, 9:16 영상을 그 안에서 재생하면 **자막이 읽히지 않는다** — 자막이
 * 이 클립의 내용 절반이라 안 보이면 답을 못 들은 것과 같다. 목록의 카드는 이제 고르는
 * 자리이고, 보는 자리는 여기다.
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
	// 끝까지 본 사람에게는 아래 버튼의 문구가 달라진다.
	const ended = useRef(false);

	if (clip === null) {
		return null;
	}

	return (
		<Modal
			open
			variant="modal--clip"
			title={clip.title ?? clip.question ?? "숏폼"}
			onClose={onClose}
		>
			<div className="clipmodal">
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
						ended.current = true;
						recordEvent("short_complete", sourceId, { clipId: clip.id });
					}}
				/>
				<div className="clipmodal__side">
					{clip.question && (
						<p className="clipmodal__q">
							<span className="clipmodal__qlabel">이 질문에 답합니다</span>
							{clip.question}
						</p>
					)}
					{clip.liked_by > 0 && (
						<p className="clipmodal__meta">{clip.liked_by}명이 궁금해했어요</p>
					)}
					{/* 🔴 **이 버튼이 이 제품의 결론이다**(tease §2-1 5단계). 숏폼은 답을 주고 끝나는
					    물건이 아니라 원본으로 데려가는 입구다. 모달을 닫고 원본의 그 초로 보낸다. */}
					<button
						type="button"
						className="button button--primary clipmodal__jump"
						onClick={() => {
							onClose();
							onJump(clip);
						}}
					>
						{ended.current ? "이어 보기" : "원본 보기"} · {time(clip.start_sec)}
					</button>
					<p className="clipmodal__hint">원본 영상의 해당 시점으로 이동합니다</p>
				</div>
			</div>
		</Modal>
	);
}
