import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
	GUTTERS,
	THEMES,
	loadGutter,
	loadTheme,
	saveGutter,
	saveTheme,
} from "../../shared/theme";
import type { Theme } from "../../shared/theme";

/**
 * 색상 고르기. 로그아웃 왼쪽에 둔다.
 *
 * 테마가 몇 개가 될지 몰라서(2026-10-24) 버튼을 늘어놓지 않고 눌러야 펼쳐지는 메뉴로 만들었다.
 * 🔴 보기 전용도 고를 수 있다 — 이 브라우저의 화면 색일 뿐 서버의 무엇도 바꾸지 않는다.
 */
export function ThemePicker() {
	return (
		<PrefMenu
			label="색상"
			options={THEMES}
			load={loadTheme}
			save={saveTheme}
			icon={(option) => <span className="sm-theme__dot" style={{ background: option.swatch }} aria-hidden="true" />}
		/>
	);
}

/** 좌우 여백 고르기. 영상 화면에만 둔다 — 홈과 프롬프트 화면은 원래 가운데 1100px 라 해당이 없다. */
export function GutterPicker() {
	return (
		<PrefMenu
			label="여백"
			options={GUTTERS}
			load={loadGutter}
			save={saveGutter}
			icon={(option) => (
				<span className="sm-theme__gutter" aria-hidden="true">
					<span style={{ marginInline: `${Number(option.id) / 2}px` }} />
				</span>
			)}
		/>
	);
}

/** 눌러서 펼치는 고르기 메뉴. 색상·여백이 같은 모양이어야 둘이 한 묶음의 설정으로 읽힌다. */
function PrefMenu({
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
