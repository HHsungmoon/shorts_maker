import { request } from "./client";

export interface AuthState {
	authenticated: boolean;
	// false 면 서버가 비밀번호 없이 떠 있다(루프백 전용 로컬 개발). 로그인 화면을 띄우지 않는다.
	authRequired: boolean;
}

export function fetchAuthState(): Promise<AuthState> {
	return request<AuthState>("/auth/me");
}

export function signIn(password: string): Promise<{ ok: boolean }> {
	return request<{ ok: boolean }>("/auth/login", { method: "POST", body: { password } });
}

export function signOut(): Promise<{ ok: boolean }> {
	return request<{ ok: boolean }>("/auth/logout", { method: "POST" });
}
