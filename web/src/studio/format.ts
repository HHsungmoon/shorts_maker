// 화면에 쓰는 서식. mm:ss 하나뿐이라 파일이 작지만, 탭을 넷으로 가르면서 세 파일이 같은
// 함수를 쓰게 됐다 — 복사해 두면 반올림 규칙이 탭마다 달라지는 게 시간 문제다.

export function time(seconds: number): string {
	const total = Math.round(seconds);
	return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
