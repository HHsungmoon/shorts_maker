import { useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
	data: T | null;
	/**
	 * **그릴 것이 아직 없다.** 이때만 "불러오는 중" 을 그린다.
	 *
	 * 🔴 "요청이 날아가는 중" 과 다르다. 이전 데이터를 들고 다시 읽는 동안에는 그릴 것이 있으므로
	 * false 다 — 그래서 기존 호출부의 `if (loading)` 가드가 저절로 옳게 동작한다.
	 */
	loading: boolean;
	/** 데이터는 있는데 뒤에서 다시 읽고 있다. 화면은 그대로 두고 표시만 살짝 줄 때 쓴다. */
	reloading: boolean;
	error: Error | null;
}

export interface AsyncOptions {
	/**
	 * 다시 읽는 동안 **이전 데이터를 유지한다** — 화면이 깜빡이지 않는다.
	 *
	 * 기본은 유지하지 않는 것(비우고 "불러오는 중"). 켜는 자리는 **폴링하는 화면**이다:
	 * 잡이 도는 동안 스튜디오는 `reloadToken` 을 올려 통째로 다시 읽는데, 그때마다 데이터가
	 * null 이 되어 패널이 매 주기 빈 화면이 됐다.
	 */
	keepPrevious?: boolean;
	/**
	 * `keepPrevious` 일 때 **무엇에 대한 데이터인가.** 이 값이 바뀌면 유지하지 않고 비운다.
	 *
	 * 🔴 없으면 다른 영상으로 옮겨도 옛 영상의 데이터가 한 프레임 남는다 — 깜빡임을 없애려다
	 * **틀린 내용을 보여주는** 것이 되고, 그건 빈 화면보다 나쁘다.
	 */
	subject?: unknown;
}

// 화면 두 개 규모라 캐싱 라이브러리 없이 이 훅으로 시작한다. 캐싱·중복요청 제거·재검증이
// 실제로 필요해지면 그때 도입한다.
export function useAsync<T>(
	load: () => Promise<T>,
	deps: unknown[],
	options: AsyncOptions = {},
): AsyncState<T> {
	const { keepPrevious = false, subject } = options;
	const [state, setState] = useState<AsyncState<T>>({
		data: null, loading: true, reloading: false, error: null,
	});
	// 지난번에 무엇을 읽고 있었는지. 대상이 바뀌었는지 판단하는 데만 쓴다.
	const subjectRef = useRef<unknown>(subject);

	useEffect(() => {
		let active = true;
		const sameSubject = Object.is(subjectRef.current, subject);
		subjectRef.current = subject;
		const keep = keepPrevious && sameSubject;

		setState((previous) => ({
			data: keep ? previous.data : null,
			// 들고 있는 것이 있으면 "불러오는 중" 이 아니다 — 그릴 것이 있다.
			loading: !(keep && previous.data !== null),
			reloading: keep && previous.data !== null,
			error: null,
		}));

		load()
			.then((data) => {
				// 응답이 늦게 도착한 이전 요청이 최신 화면을 덮어쓰지 않게 한다.
				if (active) {
					setState({ data, loading: false, reloading: false, error: null });
				}
			})
			.catch((error: unknown) => {
				if (!active) {
					return;
				}
				setState((previous) => ({
					// 🔴 폴링 중 한 번 실패했다고 화면을 비우지 않는다(keepPrevious 일 때만).
					// 잡이 도는 몇 분 동안 요청이 한 번 흔들리면 작업 화면이 통째로 사라지는데,
					// 그건 오류를 알리는 게 아니라 작업을 잃은 것처럼 보이게 한다.
					// 대신 error 를 함께 내보내 호출부가 배너로 알린다.
					data: keepPrevious ? previous.data : null,
					loading: false,
					reloading: false,
					error: error instanceof Error ? error : new Error(String(error)),
				}));
			});

		return () => {
			active = false;
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, deps);

	return state;
}
