import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

interface ModalProps {
	open: boolean;
	title: string;
	onClose: () => void;
	children: ReactNode;
	/** 모양을 바꿔야 하는 모달용(세로 영상 등). 기본 폭·여백을 덮어쓴다. */
	variant?: string;
}

/**
 * 네이티브 <dialog> 를 쓴다 — ESC 닫기, 포커스 트랩, 배경 비활성화가 기본으로 따라온다.
 *
 * 🔴 `shared/` 에 둔다. 시청자 트리와 스튜디오 트리가 **둘 다** 쓰는데, 스튜디오 쪽에 두고
 * 시청자가 가져다 쓰면 lazy 로 갈라놓은 스튜디오 코드가 시청자 번들로 딸려 온다.
 */
export function Modal({ open, title, onClose, children, variant }: ModalProps) {
	const ref = useRef<HTMLDialogElement>(null);

	useEffect(() => {
		const dialog = ref.current;
		if (!dialog) {
			return;
		}
		if (open && !dialog.open) {
			dialog.showModal();
		} else if (!open && dialog.open) {
			dialog.close();
		}
	}, [open]);

	return (
		<dialog
			ref={ref}
			className={`modal${variant ? ` ${variant}` : ""}`}
			onClose={onClose}
			onClick={(event) => {
				// 바깥(backdrop)을 눌렀을 때만 닫는다. 내용 클릭은 dialog 자신이 target 이 아니다.
				if (event.target === ref.current) {
					onClose();
				}
			}}
		>
			<div className="modal__head">
				<h2 className="modal__title">{title}</h2>
				<button type="button" className="modal__close" onClick={onClose} aria-label="닫기">
					×
				</button>
			</div>
			<div className="modal__body">{children}</div>
		</dialog>
	);
}
