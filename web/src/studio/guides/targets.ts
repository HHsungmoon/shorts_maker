// 가이드가 짚는 자리의 이름표. 문구 파일(guides/*.ts)과 화면 컴포넌트가 **같은 이름 목록**을 본다.
//
// 🔴 화면 쪽은 `data-guide="…"` 를 손으로 적지 않고 `guideAttr("…")` 로 단다. 그래야 양쪽 오타를 타입 검사가 잡는다 —
// 문자열로 두면 이름이 어긋난 단계는 화면에서 대상을 못 찾아 조용히 빠진다(guide.ts 가 없는 대상을 빼고 센다).
// 이 파일은 driver.js 를 import 하지 않는다 — 버튼 하나 다는 컴포넌트가 투어 엔진까지 끌고 오지 않게.

/** 새 자리를 짚으려면 여기에 이름을 더하고, 화면 요소에 `guideAttr` 로 단다. */
export type GuideTarget =
	// 공통 상단바
	| "guide"
	// 홈
	| "readonly"
	| "new-source"
	| "source-card"
	| "youtube"
	// 영상 화면 공통
	| "source-tabs"
	// 영상 화면 · 질문 탭
	| "q-import"
	| "q-list"
	| "q-answer"
	| "q-work"
	// 영상 화면 · 영상 준비 탭
	| "prep-steps"
	| "prep-source"
	| "prep-range"
	| "prep-captions"
	| "prep-dict"
	| "prep-segments"
	// 영상 화면 · 새 클립 탭
	| "make-ways"
	| "make-ideas"
	| "make-criteria"
	| "make-results"
	// 영상 화면 · 공개 탭
	| "pub-card"
	| "pub-tools"
	| "pub-upload"
	| "pub-waiting"
	// 영상 화면 · 유튜브 성과 탭
	| "yt-refresh"
	| "yt-totals"
	| "yt-shorts";

/** 화면 요소에 펼쳐 붙인다: `<button {...guideAttr("new-source")}>`. */
export function guideAttr(target: GuideTarget): { "data-guide": GuideTarget } {
	return { "data-guide": target };
}
