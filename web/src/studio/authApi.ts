import { request } from "../shared/client";

/**
 * 스튜디오 역할. 🔴 화면이 이걸로 버튼을 잠그지만 그건 **안내**일 뿐이다 —
 * 진짜 울타리는 서버가 메서드로 가른다(backend `http/deps.py`). 버튼을 우회해도 403 이다.
 */
export type StudioRole = "admin" | "readonly";

export interface AuthState {
	authenticated: boolean;
	// false 면 서버가 비밀번호 없이 떠 있다(루프백 전용 로컬 개발). 로그인 화면을 띄우지 않는다.
	authRequired: boolean;
	// 로그인 전에는 null 이다.
	role: StudioRole | null;
	/**
	 * 보기 전용 비밀번호. 🔴 서버가 **무인증 응답에 일부러 담아 준다** — 나눠 주려고 만든 값이라
	 * 숨기는 게 목적이 아니다. 화면에 상수로 박지 않는 이유는 `.env` 를 바꾼 순간 화면이 옛 값을
	 * 보여주고, 그걸 믿은 방문자가 못 들어오기 때문이다. 설정되지 않았으면 null 이다.
	 */
	readonlyHint: string | null;
}

export function fetchAuthState(): Promise<AuthState> {
	return request<AuthState>("/auth/me");
}

export function signIn(password: string): Promise<{ ok: boolean; role: StudioRole }> {
	return request<{ ok: boolean; role: StudioRole }>("/auth/login", {
		method: "POST",
		body: { password },
	});
}

export function signOut(): Promise<{ ok: boolean }> {
	return request<{ ok: boolean }>("/auth/logout", { method: "POST" });
}
