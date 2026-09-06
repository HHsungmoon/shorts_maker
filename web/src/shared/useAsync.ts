import { useEffect, useState } from "react";

interface AsyncState<T> {
	data: T | null;
	loading: boolean;
	error: Error | null;
}

// 화면 두 개 규모라 캐싱 라이브러리 없이 이 훅으로 시작한다. 캐싱·중복요청 제거·재검증이
// 실제로 필요해지면 그때 도입한다.
//
// 🔴 잡이 도는 동안 화면은 reloadToken 을 올려 통째로 다시 읽는다. 그래서 로딩 중에는
// data 가 null 이 되고 화면이 깜빡인다 — 폴링 주기를 줄이기 전에 여기부터 고쳐야 한다.
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> {
	const [state, setState] = useState<AsyncState<T>>({ data: null, loading: true, error: null });

	useEffect(() => {
		let active = true;
		setState({ data: null, loading: true, error: null });

		load()
			.then((data) => {
				// 응답이 늦게 도착한 이전 요청이 최신 화면을 덮어쓰지 않게 한다.
				if (active) {
					setState({ data, loading: false, error: null });
				}
			})
			.catch((error: unknown) => {
				if (active) {
					setState({
						data: null,
						loading: false,
						error: error instanceof Error ? error : new Error(String(error)),
					});
				}
			});

		return () => {
			active = false;
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, deps);

	return state;
}
