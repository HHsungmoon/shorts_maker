// 화면 설정 둘 — 색상 테마와 좌우 여백. 값(색·폭)은 shared/styles.css 의 `:root[data-theme]` ·
// `:root[data-gutter]` 블록에 있고, 여기는 목록과 저장만 한다.
//
// 🔴 고른 테마는 이 브라우저에만 남는다(localStorage). 운영자 한 명이 쓰는 도구라 서버에 둘 이유가 없고,
// 서버에 두면 테마 하나 바꾸는 데 보기 전용 권한 문제가 따라온다. 저장소가 막힌 브라우저(사생활 보호
// 창 등)에서는 기본 테마로 떨어질 뿐 화면은 멀쩡하다 — 그래서 읽기·쓰기를 전부 try 로 감싼다.

export interface Theme {
	id: string;
	label: string;
	/** 고르기 메뉴의 동그라미 색. styles.css 의 그 테마 --accent 와 같은 값이다. */
	swatch: string;
}

/** 첫 줄이 기본 테마다(`data-theme` 없음). 새 테마는 styles.css 블록과 함께 여기 한 줄을 더한다. */
export const THEMES: Theme[] = [
	{ id: "blue", label: "CLIPQ 블루", swatch: "#4466cc" },
	{ id: "sky", label: "스카이", swatch: "#0ea5e9" },
	{ id: "green", label: "그린", swatch: "#1f6f4a" },
	{ id: "violet", label: "바이올렛", swatch: "#6a4cc9" },
];

const KEY = "clipq-theme";

export function loadTheme(): string {
	try {
		const saved = localStorage.getItem(KEY);
		if (saved && THEMES.some((t) => t.id === saved)) {
			return saved;
		}
	} catch {
		// 저장소를 못 쓰면 기본 테마.
	}
	return THEMES[0].id;
}

export function applyTheme(id: string): void {
	if (id === THEMES[0].id) {
		delete document.documentElement.dataset.theme;
	} else {
		document.documentElement.dataset.theme = id;
	}
}

export function saveTheme(id: string): void {
	applyTheme(id);
	try {
		localStorage.setItem(KEY, id);
	} catch {
		// 이번 화면에만 적용된다. 새로고침하면 기본으로 돌아간다.
	}
}

// ---------------------------------------------------------------- 좌우 여백

/**
 * 영상 화면 본문의 좌우 여백. 화면 전체 폭을 쓰게 바꿨더니(2026-10-24) 넓은 모니터에서 부담스럽다는
 * 피드백이 있었다 — 기본은 5%, 고를 수 있게 둔다. 🔴 로고 줄에는 걸지 않는다. 제목 줄부터 아래만이다.
 */
export const GUTTERS: Theme[] = [
	{ id: "5", label: "5%", swatch: "" },
	{ id: "0", label: "없음 (화면 가득)", swatch: "" },
	{ id: "10", label: "10%", swatch: "" },
];

const GUTTER_KEY = "clipq-gutter";

export function loadGutter(): string {
	try {
		const saved = localStorage.getItem(GUTTER_KEY);
		if (saved && GUTTERS.some((g) => g.id === saved)) {
			return saved;
		}
	} catch {
		// 저장소를 못 쓰면 기본 여백.
	}
	return GUTTERS[0].id;
}

export function applyGutter(id: string): void {
	if (id === GUTTERS[0].id) {
		delete document.documentElement.dataset.gutter;
	} else {
		document.documentElement.dataset.gutter = id;
	}
}

export function saveGutter(id: string): void {
	applyGutter(id);
	try {
		localStorage.setItem(GUTTER_KEY, id);
	} catch {
		// 이번 화면에만 적용된다.
	}
}
