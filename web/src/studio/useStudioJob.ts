import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "./AuthContext";
import { fetchJob, fetchStatus } from "./api";
import type { ShortsJob } from "./types";

// 두 화면(홈 · 영상)이 함께 쓰는 잡 상태 — 띄우기·폴링·끝나면 다시 읽기.
//
// 🔴 잡 배너를 여기로 올린 이유는 홈에서 잡이 **시작되기** 때문이다 — 유튜브에서 받아 등록하는
// 데 몇 분이 걸리는데, 그 진행이 영상 화면에서만 보이면 방금 등록을 누른 사람은 아무 반응도
// 못 본다. 그래서 잡 상태·폴링·reloadToken 을 훅 하나로 묶어 두 화면이 각자 들고 있게 했다.
// 전역 상태로 올리지 않은 건 잡이 화면을 넘어 이어질 일이 없어서다 — 홈에서 띄운 잡은 홈에서
// 끝나고, 그 결과는 reloadToken 이 올라가며 목록에 반영된다.

export const STAGE_LABEL: Record<string, string> = {
	download: "영상 받기",
	register: "원본 등록",
	chunk: "구간 추출",
	stt: "음성 인식",
	segment: "주제 분할",
	rank: "선정",
	cut: "클립 만들기",
	render: "렌더",
	pipeline: "전체 실행",
	cluster: "질문 집계",
	answer: "질문에 답하기",
};

export interface StudioJob {
	job: ShortsJob | null;
	/** 이 탭이 띄운 잡이 아직 도는 중. 버튼을 잠그는 근거다. */
	jobBusy: boolean;
	/**
	 * **다른 창·다른 사람이** 돌리고 있는 잡. 서버는 한 번에 하나만 받으므로(409) 이게 있으면
	 * 지금 누르는 실행은 거절된다. 이 탭이 띄운 잡이면 null 이다 — 그건 `job` 이 이미 말한다.
	 */
	foreignJob: ShortsJob | null;
	/** 올라갈 때마다 화면이 서버를 다시 읽는다(useAsync 의 deps). */
	reloadToken: number;
	reload: () => void;
	error: string | null;
	setError: (message: string | null) => void;
	notice: string | null;
	setNotice: (message: string | null) => void;
	/** 잡을 띄운다 — 진행 배너와 폴링이 여기에 딸려 온다. */
	submit: (start: () => Promise<ShortsJob>) => void;
	/** 다른 데서 이미 띄운 잡(NewSourceModal)을 배너와 폴링에 얹는다. */
	adopt: (job: ShortsJob) => void;
	/** 잡이 아닌 즉시 호출(발행·제목 수정 …). 끝나면 화면을 다시 읽는다. */
	act: (run: () => Promise<unknown>) => void;
}

export function useStudioJob(): StudioJob {
	// 🔴 실행을 **여기서** 막는다. 버튼마다 막으면 새로 생긴 버튼을 반드시 빠뜨린다 —
	// 스튜디오의 실행은 거의 전부 이 훅의 submit·act 를 지난다(서버에도 같은 경계가 있다).
	const { canAct, refuse } = useAuth();
	const [job, setJob] = useState<ShortsJob | null>(null);
	const [reloadToken, setReloadToken] = useState(0);
	const [error, setError] = useState<string | null>(null);
	const [notice, setNotice] = useState<string | null>(null);
	const [activeJob, setForeignJob] = useState<ShortsJob | null>(null);
	const pollRef = useRef<number | null>(null);

	const jobBusy = job !== null && job.status !== "DONE" && job.status !== "FAILED";

	useEffect(() => {
		if (!job || !jobBusy) {
			return;
		}
		pollRef.current = window.setTimeout(async () => {
			try {
				const next = await fetchJob(job.id);
				setJob(next);
				if (next.status === "DONE" || next.status === "FAILED") {
					setReloadToken((n) => n + 1);
					setError(next.error);
				}
			} catch (e: unknown) {
				setError(e instanceof Error ? e.message : String(e));
			}
		}, 2000);
		return () => {
			if (pollRef.current) {
				window.clearTimeout(pollRef.current);
			}
		};
	}, [job, jobBusy]);

	// 🔴 남이 돌리는 잡을 5초마다 확인한다. 화면 데이터(useAsync)는 내가 뭔가 했을 때만 다시
	// 읽히는데, 다른 사람의 실행은 내 행동과 무관하게 시작된다 — 그래서 별도로 본다.
	// 이게 없으면 "왜 내 실행만 거절되지" 가 되고, 원인이 화면 어디에도 없다.
	// 가벼운 GET 하나고 보기 전용 세션도 부를 수 있다(읽기라 403 이 아니다).
	useEffect(() => {
		let active = true;
		const look = async () => {
			try {
				const status = await fetchStatus();
				if (active) {
					setForeignJob(status.activeJob);
				}
			} catch {
				// 상태를 못 읽는 건 배너 하나가 빠지는 일이다. 화면을 깨뜨리지 않는다.
			}
		};
		look();
		const timer = window.setInterval(look, 5000);
		return () => {
			active = false;
			window.clearInterval(timer);
		};
	}, []);

	const reload = useCallback(() => setReloadToken((n) => n + 1), []);

	const submit = useCallback(async (start: () => Promise<ShortsJob>) => {
		if (!canAct) {
			refuse();
			return;
		}
		setError(null);
		setNotice(null);
		try {
			setJob(await start());
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		}
	}, [canAct, refuse]);

	const adopt = useCallback((started: ShortsJob) => {
		setError(null);
		setNotice(null);
		setJob(started);
	}, []);

	const act = useCallback(async (run: () => Promise<unknown>) => {
		if (!canAct) {
			refuse();
			return;
		}
		setError(null);
		try {
			await run();
			setReloadToken((n) => n + 1);
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		}
	}, [canAct, refuse]);

	return {
		job,
		jobBusy,
		// 내가 띄운 잡은 `job` 이 말한다 — 같은 것을 두 배너로 두 번 알리지 않는다.
		foreignJob: activeJob && activeJob.id !== job?.id ? activeJob : null,
		reloadToken,
		reload,
		error,
		setError,
		notice,
		setNotice,
		submit,
		adopt,
		act,
	};
}
