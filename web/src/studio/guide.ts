// 가이드 보기 — 화면 위에 사용법을 차례로 띄우는 프로덕트 투어(2026-11-15).
//
// 화면을 어둡게 덮고 요소 하나만 뚫어 보여 주며 옆 말풍선에서 [다음] 으로 넘긴다. 그리는 일은 driver.js 가 한다 —
// 말풍선 위치 계산 · 스크롤 따라가기 · 창 크기 변화 · 키보드(← → Esc)를 직접 짜는 것보다 싸다.
// 🔴 driver.js 를 고른 근거는 런타임 동작이다(CLAUDE.md 규약): 의존성이 없고(React 를 모른다 — React 를 올려도
// 같이 깨지지 않는다), 스튜디오 청크에만 약 8KB(gzip) 붙는다. React Joyride 는 React 내부에 기대서 React 19 지원이
// 늦었던 이력이 있다.
// 🔴 버전을 정확히 고정한다(package.json 에 ^ 없음). 2026-11 에 닷새 사이 다섯 번 배포됐고 저장소도 다른 조직으로
// 옮겨졌다 — 갓 나온 판을 그대로 받지 않고 나온 지 2주가 넘은 1.10.2(2026-10-18)를 쓴다. 올릴 때도 같은 기준이다.
//
// 짚을 대상은 클래스가 아니라 `data-guide` 속성으로 찾는다. 클래스는 디자인을 고칠 때 바뀌고, 그러면 안내가
// 조용히 빈 단계를 띄운다. 속성 이름은 "이 자리를 가이드가 짚는다" 는 표시이기도 하다(guides/targets.ts).
//
// 문구는 화면마다 `guides/<화면>.ts` 에 따로 둔다(2026-11-15) — 컴포넌트를 열지 않고 문구만 고칠 수 있게.
// 나중에 배포 없이 고쳐야 하면 그 파일의 내용을 그대로 서버로 옮긴다.

import { useCallback, useEffect, useRef } from "react";
import { driver } from "driver.js";
import type { Alignment, Driver, DriveStep, Side } from "driver.js";
import "driver.js/dist/driver.css";
import type { GuideTarget } from "./guides/targets";
import "./guide.css";

/** 안내 한 단계. `target` 이 없으면 화면 가운데에 말풍선만 뜬다(시작 인사 같은 것). */
export interface GuideStep {
	/** 짚을 요소의 이름표(guides/targets.ts). 같은 이름이 여럿이면 화면에서 첫 번째 것을 짚는다. */
	target?: GuideTarget;
	title: string;
	/** 순수 글. 꾸밈은 둘뿐이다 — `**굵게**` 와 줄바꿈(`\n`). HTML 태그를 적으면 글자 그대로 보인다. */
	body: string;
	side?: Side;
	align?: Alignment;
}

function selector(target: GuideTarget): string {
	return `[data-guide="${target}"]`;
}

/**
 * 문구 → 말풍선 HTML. 🔴 driver.js 는 제목·본문을 innerHTML 로 넣는다. 그래서 **먼저 전부 이스케이프하고**
 * 우리가 허락한 두 가지(굵게 · 줄바꿈)만 태그로 되살린다. 문구가 나중에 서버에서 오더라도 이 한 곳이 울타리다.
 */
function toHtml(text: string): string {
	return text
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
		.replace(/\n/g, "<br>");
}

/**
 * 지금 화면에 맞춰 안내를 시작한다.
 *
 * 🔴 대상이 없는 단계는 **미리 뺀다**. 같은 화면이라도 상태에 따라 없는 요소가 있다 — 영상이 없으면 카드가,
 * 관리자로 들어오면 "보기 전용" 표시가 없다. driver.js 의 skipMissingElement 로 건너뛰게 하면 진행 표시가
 * "3 / 9" 에서 "5 / 9" 로 뛴다. 빼고 세야 숫자가 맞는다.
 */
function start(steps: GuideStep[]): Driver {
	const present: DriveStep[] = steps
		.filter((step) => !step.target || document.querySelector(selector(step.target)))
		.map((step) => ({
			element: step.target ? selector(step.target) : undefined,
			popover: { title: toHtml(step.title), description: toHtml(step.body), side: step.side, align: step.align },
		}));

	const tour = driver({
		steps: present,
		popoverClass: "sm-guide",
		showProgress: true,
		progressText: "{{current}} / {{total}}",
		nextBtnText: "다음",
		prevBtnText: "이전",
		doneBtnText: "완료",
		closeBtnLabel: "안내 닫기",
		// 덮개를 너무 짙게 하면 짚은 자리 둘레가 안 읽혀 "어디쯤인지" 를 잃는다.
		overlayOpacity: 0.55,
		stagePadding: 6,
		stageRadius: 12,
		popoverOffset: 12,
		// 🔴 짚은 요소를 누르지 못하게 한다. 카드를 짚은 채로 누르면 영상 화면으로 넘어가고, 덮개와 말풍선은 사라진
		// 요소를 가리킨 채 남는다. 안내 중에는 읽기만 한다.
		disableActiveInteraction: true,
	});
	tour.drive();
	return tour;
}

/**
 * 화면 하나의 안내를 여는 함수를 돌려준다. 화면을 떠나면(언마운트) 열려 있던 안내를 닫는다 —
 * 브라우저 뒤로가기로 나가면 덮개가 다음 화면 위에 남는다.
 */
export function useGuide(steps: GuideStep[]): () => void {
	const tour = useRef<Driver | null>(null);
	useEffect(
		() => () => {
			tour.current?.destroy();
		},
		[],
	);
	return useCallback(() => {
		// 두 번 누르면 처음부터 다시 — 겹쳐 띄우지 않는다.
		tour.current?.destroy();
		tour.current = start(steps);
	}, [steps]);
}
