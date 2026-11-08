import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

/**
 * 연한 글자 단추 하나 + 누르면 뜨는 말풍선. 화면에 늘 깔아 둘 필요가 없는 설명(쓰는 법 · 단축키)을 여기에 넣는다.
 *
 * 2026-11-08: 편집 화면의 설명 줄이 칸마다 붙어 화면이 지저분해졌다 — 설명은 접어 두고 필요할 때 연다.
 * 바깥을 누르거나 Esc 로 닫는다. 모달로 만들지 않는다 — 설명을 보면서 그 옆의 칸을 만질 수 있어야 한다.
 *
 * 🔴 `shared/` 에 둔다(Modal 과 같은 이유) — 어느 트리든 같은 모양의 설명 단추를 쓴다.
 */
export function Hint({
	label = "설명 보기",
	align = "end",
	children,
}: {
	label?: string;
	/** 말풍선을 단추의 어느 쪽 끝에 맞출까. 화면 오른쪽 가장자리의 단추는 "end". */
	align?: "start" | "end";
	children: ReactNode;
}) {
	const [open, setOpen] = useState(false);
	const box = useRef<HTMLSpanElement>(null);
	const popId = useId();

	useEffect(() => {
		if (!open) {
			return;
		}
		const onDown = (event: PointerEvent) => {
			if (box.current && !box.current.contains(event.target as Node)) {
				setOpen(false);
			}
		};
		const onKey = (event: KeyboardEvent) => {
			if (event.key === "Escape") {
				setOpen(false);
			}
		};
		document.addEventListener("pointerdown", onDown);
		document.addEventListener("keydown", onKey);
		return () => {
			document.removeEventListener("pointerdown", onDown);
			document.removeEventListener("keydown", onKey);
		};
	}, [open]);

	return (
		<span className="hint" ref={box}>
			<button
				type="button"
				className="hint__button"
				aria-expanded={open}
				aria-controls={popId}
				onClick={() => setOpen((value) => !value)}
			>
				{label}
			</button>
			{open && (
				<span id={popId} role="note" className={`hint__pop hint__pop--${align}`}>
					{children}
				</span>
			)}
		</span>
	);
}
