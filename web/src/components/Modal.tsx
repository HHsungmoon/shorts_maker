import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

interface ModalProps {
	open: boolean;
	title: string;
	onClose: () => void;
	children: ReactNode;
}

// 네이티브 <dialog> 를 쓴다 — ESC 닫기, 포커스 트랩, 배경 비활성화가 기본으로 따라온다.
export function Modal({ open, title, onClose, children }: ModalProps) {
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
			className="modal"
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
