import type { Theme } from "../shared/theme";

// 시청자 영상 화면의 레이아웃. 🔴 고른 값은 이 브라우저에만 남는다(localStorage) — 시청자는 로그인이
// 없어서 서버에 둘 자리가 없다. 저장소가 막히면 기본 레이아웃으로 떨어질 뿐이라 읽기·쓰기를 try 로 감싼다.
//
// 둘이다(2026-10-24, 디자인 캔버스 G 줄):
// - shelf   — 플레이어 아래에 "답이 나온 자리" 표시와 숏폼 선반(가로 3개), 오른쪽에 질문이 길게 내려온다(G3).
// - classic — 예전 배치. 오른쪽 단에 숏폼 2열, 왼쪽 플레이어 아래 질문.
// 🔴 기존 배치를 지우지 않고 고를 수 있게 둔 것은 사용자 요청이다 — 새 배치를 써 보고 되돌릴 길을 남긴다.

export type WatchLayout = "shelf" | "classic";

/** 첫 줄이 기본이다. */
export const WATCH_LAYOUTS: Theme[] = [
	{ id: "shelf", label: "숏폼 선반", swatch: "" },
	{ id: "classic", label: "기존 배치", swatch: "" },
];

const KEY = "clipq-watch-layout";

export function loadWatchLayout(): WatchLayout {
	try {
		const saved = localStorage.getItem(KEY);
		if (saved && WATCH_LAYOUTS.some((l) => l.id === saved)) {
			return saved as WatchLayout;
		}
	} catch {
		// 저장소를 못 쓰면 기본 레이아웃.
	}
	return WATCH_LAYOUTS[0].id as WatchLayout;
}

export function saveWatchLayout(id: string): void {
	try {
		localStorage.setItem(KEY, id);
	} catch {
		// 이번 화면에만 적용된다.
	}
}
