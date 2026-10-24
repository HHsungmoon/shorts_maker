import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Theme } from "./theme";

/**
 * 눌러서 펼치는 고르기 메뉴. 색상·여백이 같은 모양이어야 둘이 한 묶음의 설정으로 읽힌다.
 * 시청자 화면의 레이아웃 고르기도 이걸 쓴다 — 그래서 studio 가 아니라 shared 에 있다.
 */
export function PrefMenu({
	label,
	options,
	load,
	save,
	icon,
}: {
	label: string;
	options: Theme[];
	load: () => string;
	save: (id: string) => void;
	icon: (option: Theme) => ReactNode;
}) {
	const [current, setCurrent] = useState(load);
	const [open, setOpen] = useState(false);
	const root = useRef<HTMLDivElement>(null);

	// 밖을 누르거나 Esc 면 닫는다. 열려 있을 때만 듣는다.
	useEffect(() => {
		if (!open) {
			return;
		}
		function onDown(event: MouseEvent) {
			if (!root.current?.contains(event.target as Node)) {
				setOpen(false);
			}
		}
		function onKey(event: KeyboardEvent) {
			if (event.key === "Escape") {
				setOpen(false);
			}
		}
		document.addEventListener("mousedown", onDown);
		document.addEventListener("keydown", onKey);
		return () => {
			document.removeEventListener("mousedown", onDown);
			document.removeEventListener("keydown", onKey);
		};
	}, [open]);

	const active = options.find((o) => o.id === current) ?? options[0];

	function pick(id: string) {
		save(id);
		setCurrent(id);
		setOpen(false);
	}

	return (
		<div className="sm-theme" ref={root}>
			<button
				type="button"
				className="button button--small sm-theme__toggle"
				aria-haspopup="menu"
				aria-expanded={open}
				onClick={() => setOpen((v) => !v)}
			>
				{icon(active)}
				{label}
			</button>
			{open && (
				<ul className="sm-theme__menu" role="menu" aria-label={label}>
					{options.map((option) => (
						<li key={option.id} role="none">
							<button
								type="button"
								role="menuitemradio"
								aria-checked={option.id === current}
								className="sm-theme__item"
								onClick={() => pick(option.id)}
							>
								{icon(option)}
								{option.label}
								{option.id === current && (
									<span className="sm-theme__check" aria-hidden="true">
										✓
									</span>
								)}
							</button>
						</li>
					))}
				</ul>
			)}
		</div>
	);
}
