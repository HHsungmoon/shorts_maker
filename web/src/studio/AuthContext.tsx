import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { fetchAuthState, signIn as postSignIn, signOut as postSignOut } from "./authApi";
import { onUnauthorized } from "../shared/client";

// 🔴 세션 쿠키가 httpOnly 라 JS 로는 읽을 수 없다 — 그게 목적이다(XSS 가 나도 세션이
// 바로 새지 않는다). 그래서 로그인 여부를 로컬에서 판단하지 않고 서버에 물어본다.
//
// 상태가 셋인 이유: 첫 확인이 끝나기 전에 "로그아웃"으로 단정하면 새로고침할 때마다
// 로그인 화면이 한 번 번쩍인다.
type Status = "checking" | "in" | "out";

interface AuthValue {
	status: Status;
	// 서버가 비밀번호 없이 떠 있으면(로컬 개발) 로그아웃 버튼을 숨긴다 — 누를 수 없는 버튼이다.
	authRequired: boolean;
	signIn: (password: string) => Promise<void>;
	signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
	const [status, setStatus] = useState<Status>("checking");
	const [authRequired, setAuthRequired] = useState(true);

	useEffect(() => {
		let active = true;
		fetchAuthState()
			.then((state) => {
				if (!active) {
					return;
				}
				setAuthRequired(state.authRequired);
				setStatus(state.authenticated ? "in" : "out");
			})
			// 서버가 죽어 있어도 화면은 떠야 한다. 로그인 화면에서 실패 이유를 보여준다.
			.catch(() => active && setStatus("out"));
		return () => {
			active = false;
		};
	}, []);

	// 세션이 만료되면 아무 요청에서나 401 이 온다. 그 지점에서 한 번에 로그인 화면으로 되돌린다 —
	// 호출부마다 401 을 따로 처리하면 반드시 빠뜨리는 곳이 생긴다.
	useEffect(() => {
		onUnauthorized(() => setStatus("out"));
		return () => onUnauthorized(null);
	}, []);

	const signIn = useCallback(async (password: string) => {
		await postSignIn(password);
		setStatus("in");
	}, []);

	const signOut = useCallback(async () => {
		try {
			await postSignOut();
		} finally {
			// 서버 호출이 실패해도 화면은 로그아웃시킨다. 여기서 막으면 사용자가 빠져나갈 방법이 없다.
			setStatus("out");
		}
	}, []);

	return (
		<AuthContext.Provider value={{ status, authRequired, signIn, signOut }}>
			{children}
		</AuthContext.Provider>
	);
}

export function useAuth(): AuthValue {
	const value = useContext(AuthContext);
	if (!value) {
		throw new Error("useAuth 는 AuthProvider 안에서만 쓴다");
	}
	return value;
}
