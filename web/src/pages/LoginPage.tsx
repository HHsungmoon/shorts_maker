import { useState } from "react";
import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
	const { signIn } = useAuth();
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
				<h1 className="login__title">shorts_maker</h1>
				<p className="sm-meta" style={{ textAlign: "center", marginTop: -8 }}>
					긴 영상에서 숏폼 클립을 뽑아냅니다
				</p>

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
