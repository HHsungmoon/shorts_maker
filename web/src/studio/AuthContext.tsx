import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { fetchAuthState, signIn as postSignIn, signOut as postSignOut } from "./authApi";
import type { StudioRole } from "./authApi";
import { onForbidden, onUnauthorized } from "../shared/client";

// 🔴 세션 쿠키가 httpOnly 라 JS 로는 읽을 수 없다 — 그게 목적이다(XSS 가 나도 세션이
// 바로 새지 않는다). 그래서 로그인 여부를 로컬에서 판단하지 않고 서버에 물어본다.
//
// 상태가 셋인 이유: 첫 확인이 끝나기 전에 "로그아웃"으로 단정하면 새로고침할 때마다
// 로그인 화면이 한 번 번쩍인다.
type Status = "checking" | "in" | "out";

/**
 * 보기 전용이 막힌 것을 눌렀을 때의 문구.
 *
 * 🔴 여기(권한)에 둔다. 예전에는 잡 훅(useStudioJob)에 있었는데, 잡과 상관없는 자리(삭제·기준
 * 저장·클러스터 패널)도 같은 문구를 쓰면서 그쪽이 훅을 import 하는 이상한 의존이 생겼다.
 */
export const READ_ONLY_NOTICE = "보기 전용으로 로그인했습니다. 이 동작은 관리자만 할 수 있습니다.";

interface AuthValue {
	status: Status;
	// 서버가 비밀번호 없이 떠 있으면(로컬 개발) 로그아웃 버튼을 숨긴다 — 누를 수 없는 버튼이다.
	authRequired: boolean;
	role: StudioRole | null;
	/**
	 * 실행·발행·수정을 할 수 있는가.
	 *
	 * 🔴 이건 **안내용**이다. 진짜 경계는 서버에 있다(backend `http/deps.py` — GET 말고는 전부
	 * 거절). 화면이 잠그는 이유는 누를 수 없는 버튼을 누르게 두면 403 만 쌓이기 때문이다.
	 */
	canAct: boolean;
	/** 로그인 화면에 적어 주는 보기 전용 비밀번호. 서버가 준다(authApi.AuthState). */
	readonlyHint: string | null;
	/**
	 * 지금 보여줄 거절 안내. 서버가 준 403 이거나, 화면이 미리 막은 것이다.
	 * 🔴 한 곳에 모은다 — 화면마다 따로 알리면 어떤 버튼은 배너, 어떤 버튼은 아무 반응도 없게 된다.
	 */
	denied: string | null;
	/** 막힌 것을 눌렀다고 알린다. 기본 문구는 READ_ONLY_NOTICE. */
	refuse: (message?: string) => void;
	clearDenied: () => void;
	signIn: (password: string) => Promise<void>;
	signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
	const [status, setStatus] = useState<Status>("checking");
	const [authRequired, setAuthRequired] = useState(true);
	const [role, setRole] = useState<StudioRole | null>(null);
	const [readonlyHint, setReadonlyHint] = useState<string | null>(null);
	const [denied, setDenied] = useState<string | null>(null);

	useEffect(() => {
		let active = true;
		fetchAuthState()
			.then((state) => {
				if (!active) {
					return;
				}
				setAuthRequired(state.authRequired);
				setRole(state.role);
				setReadonlyHint(state.readonlyHint);
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

	// 403 은 로그아웃이 아니다. 화면은 그대로 두고 왜 안 됐는지만 알린다.
	useEffect(() => {
		onForbidden(setDenied);
		return () => onForbidden(null);
	}, []);

	// 🔴 안정적인 함수여야 한다 — 이 값을 의존성에 넣는 useCallback 들이 매 렌더 다시 만들어진다.
	const refuse = useCallback((message?: string) => setDenied(message ?? READ_ONLY_NOTICE), []);
	// 🔴 인라인 화살표로 두면 매 렌더 새 함수가 되고, 이걸 의존성에 넣은 토스트의 자동 닫기
	// 타이머가 그때마다 처음부터 다시 시작된다(= 영영 안 닫힌다).
	const clearDenied = useCallback(() => setDenied(null), []);

	const signIn = useCallback(async (password: string) => {
		const result = await postSignIn(password);
		// 비밀번호 하나로 역할이 갈린다 — 어느 문으로 들어왔는지 서버가 알려준다.
		setRole(result.role);
		setDenied(null);
		setStatus("in");
	}, []);

	const signOut = useCallback(async () => {
		try {
			await postSignOut();
		} finally {
			// 서버 호출이 실패해도 화면은 로그아웃시킨다. 여기서 막으면 사용자가 빠져나갈 방법이 없다.
			setRole(null);
			setDenied(null);
			setStatus("out");
		}
	}, []);

	return (
		<AuthContext.Provider
			value={{
				status,
				authRequired,
				role,
				// 비밀번호를 안 쓰는 로컬 개발(authRequired=false)에서는 전권이다.
				canAct: role !== "readonly",
				readonlyHint,
				denied,
				refuse,
				clearDenied,
				signIn,
				signOut,
			}}
		>
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
