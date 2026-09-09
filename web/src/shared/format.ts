// 화면에 쓰는 서식. 지금은 시간 하나뿐이다.
//
// 원래 `studio/format.ts` 였는데 시청자 화면도 같은 함수를 쓰게 돼서 여기로 올렸다 —
// 복사해 두면 반올림 규칙이 화면마다 달라지는 게 시간 문제다.

/**
 * 초를 사람이 읽는 시간으로. 1시간을 넘으면 시간 자리가 생긴다.
 *
 * 🔴 시간 자리를 넣은 이유: 우리가 다루는 원본은 강연이라 1~2시간이 보통이다. 95분 영상의
 * 마지막 구간을 `95:00` 으로 적으면 그게 몇 시 몇 분인지 사람이 계산해야 하고, 유튜브
 * 플레이어에 적힌 `1:35:00` 과도 달라 보인다 — 원본으로 보내는 버튼에 붙는 숫자라서
 * 플레이어와 같은 표기여야 한다.
 */
export function time(seconds: number): string {
	const total = Math.max(0, Math.round(seconds));
	const secs = String(total % 60).padStart(2, "0");
	const mins = Math.floor(total / 60) % 60;
	const hours = Math.floor(total / 3600);
	if (hours === 0) {
		return `${mins}:${secs}`;
	}
	return `${hours}:${String(mins).padStart(2, "0")}:${secs}`;
}
