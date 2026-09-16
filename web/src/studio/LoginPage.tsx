import { useState } from "react";
import { useAuth } from "./AuthContext";
import { PRODUCT_NAME } from "../shared/brand";

export function LoginPage() {
	const { signIn, readonlyHint } = useAuth();
	const [password, setPassword] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	async function submit(event: React.FormEvent) {
		event.preventDefault();
		setBusy(true);
		setError(null);
		try {
			await signIn(password);
		} catch (e: unknown) {
			// 서버가 이유를 구분해서 준다: 비밀번호 불일치(401)와 잠금(429)은 다른 안내가 필요하다.
			setError(e instanceof Error ? e.message : String(e));
			setPassword("");
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="login">
			<form className="login__card" onSubmit={submit}>
				<h1 className="login__title">{PRODUCT_NAME}</h1>
				<p className="sm-meta" style={{ textAlign: "center", marginTop: -8 }}>
					긴 영상에서 숏폼 클립을 뽑아냅니다
				</p>
				{/* 문이 둘이라는 걸 로그인 화면에서 말해 준다 — 보기 전용 비밀번호를 받은 사람이
				    "내 비밀번호가 틀렸나" 로 헤매지 않게. */}
				<p className="sm-meta" style={{ textAlign: "center" }}>
					보기 전용 비밀번호로 들어오면 화면은 전부 볼 수 있고 실행만 막힙니다.
				</p>
				{/* 🔴 비밀번호를 화면에 대놓고 적는다. 구경하러 온 사람에게 나눠 주려고 만든 값이라
				    숨기면 쓸모가 없다 — 대신 그 역할로는 GET 말고 아무것도 되지 않는다(서버가 403).
				    값은 서버가 준다(AuthContext) — 여기 상수로 박으면 `.env` 를 바꾼 뒤 화면이
				    옛 값을 보여주고, 그걸 믿은 사람이 못 들어온다. */}
				{readonlyHint && (
					<p className="sm-meta" style={{ textAlign: "center" }}>
						구경만 하실 분: <code>{readonlyHint}</code>
					</p>
				)}

				<div className="field">
					<label className="field__label" htmlFor="password">
						비밀번호
					</label>
					<input
						id="password"
						className="field__input"
						type="password"
						value={password}
						autoFocus
						// 브라우저 비밀번호 관리자가 저장을 제안하게 둔다. 계정이 하나뿐이라
						// 사람이 외우기 쉬운 비밀번호를 쓰게 되는 게 더 나쁘다.
						autoComplete="current-password"
						onChange={(event) => setPassword(event.target.value)}
					/>
				</div>

				{error && <p className="login__error">{error}</p>}

				<button
					type="submit"
					className="button button--primary"
					disabled={busy || password.length === 0}
				>
					{busy ? "확인 중…" : "로그인"}
				</button>
			</form>
		</div>
	);
}
