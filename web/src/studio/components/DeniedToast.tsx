import { useEffect } from "react";
import { useAuth } from "../AuthContext";

/** 스스로 사라지는 시간. 한 문장이라 읽는 데 그리 오래 걸리지 않는다. */
const DISMISS_MS = 5000;

/**
 * 보기 전용이 막힌 것을 눌렀을 때 화면 위에 뜨는 띠.
 *
 * 🔴 처음에는 모달이었는데 과했다(2026-09-17). 한 줄 안내에 화면을 덮고 닫기를 강요하면,
 * 구경하러 온 사람이 버튼을 누를 때마다 흐름이 끊긴다. 토스트는 읽히되 막지 않는다.
 *
 * 🔴 그래도 배너로는 돌아가지 않는다. 배너는 본문 맨 위에 있어서 아래쪽 버튼을 누른 사람
 * 눈에 안 들어왔다 — 그게 애초에 "아무 일도 안 일어난다" 는 말이 나온 이유다. 이 띠는
 * 화면에 고정되어 어디서 눌렀든 같은 자리에 뜬다.
 *
 * 레이아웃을 밀지 않도록 fixed 다. 띠가 뜰 때 본문이 내려가면 방금 누른 버튼이 움직인다.
 */
export function DeniedToast() {
	const { denied, clearDenied } = useAuth();

	useEffect(() => {
		if (denied === null) {
			return;
		}
		const timer = window.setTimeout(clearDenied, DISMISS_MS);
		return () => window.clearTimeout(timer);
	}, [denied, clearDenied]);

	if (denied === null) {
		return null;
	}

	return (
		// role="status" + aria-live: 화면 낭독기가 지금 하던 말을 끊지 않고 이어서 읽는다.
		<div className="toast" role="status" aria-live="polite">
			<span>{denied}</span>
			<button type="button" className="toast__close" onClick={clearDenied} aria-label="닫기">
				×
			</button>
		</div>
	);
}
