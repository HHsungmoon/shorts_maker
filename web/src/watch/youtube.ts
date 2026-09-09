// 유튜브 IFrame Player API 로더.
//
// 🔴 **이 파일이 있는 이유.** 평범한 `<iframe src=".../embed/ID">` 로는 재생 위치를 옮길 수 없다.
// 이 제품의 결론은 "숏폼으로 답을 보고, 그 이야기가 있는 원본의 그 지점으로 들어간다" 인데
// (tease §2-1 5단계), "그 지점으로" 를 하려면 플레이어에 명령을 보내야 한다. 그래서 API 를 쓴다.
//
// 새 탭으로 유튜브를 여는 것과는 다른 일이다. 페이지를 떠나면 질문 패널도 숏폼 목록도 사라져서
// 이 서비스로 돌아올 이유가 없어진다 — 유입을 만들려고 이탈을 만드는 셈이 된다.

const SCRIPT_SRC = "https://www.youtube.com/iframe_api";

/** 우리가 실제로 부르는 것만 적는다. 타입 패키지를 받지 않는 이유는 이게 전부이기 때문이다. */
export interface YouTubePlayer {
	seekTo(seconds: number, allowSeekAhead: boolean): void;
	playVideo(): void;
	destroy(): void;
}

export interface YouTubeStateEvent {
	data: number;
}

export interface YouTubePlayerOptions {
	videoId: string;
	/** 임베드를 받아올 도메인. nocookie 를 쓴다 — tease §8-3. */
	host?: string;
	playerVars?: Record<string, string | number>;
	events?: {
		onReady?: () => void;
		onStateChange?: (event: YouTubeStateEvent) => void;
		onError?: () => void;
	};
}

export interface YouTubeApi {
	Player: new (host: HTMLElement | string, options: YouTubePlayerOptions) => YouTubePlayer;
	PlayerState: { UNSTARTED: number; ENDED: number; PLAYING: number; PAUSED: number; BUFFERING: number };
}

declare global {
	interface Window {
		YT?: YouTubeApi;
		onYouTubeIframeAPIReady?: () => void;
	}
}

// 🔴 약속(promise)을 캐시한다. 유튜브가 준비를 알리는 통로는 `window.onYouTubeIframeAPIReady`
// **전역 하나**라서, 두 곳에서 각자 등록하면 나중에 등록한 쪽이 먼저 등록한 쪽을 덮어쓰고
// 먼저 등록한 쪽은 영원히 안 불린다. 등록은 이 모듈이 한 번만 하고 모두 같은 약속을 기다린다.
let pending: Promise<YouTubeApi> | null = null;

export function loadYouTubeApi(): Promise<YouTubeApi> {
	if (pending) {
		return pending;
	}
	const loading = new Promise<YouTubeApi>((resolve, reject) => {
		if (window.YT?.Player) {
			resolve(window.YT);
			return;
		}
		window.onYouTubeIframeAPIReady = () => {
			if (window.YT?.Player) {
				resolve(window.YT);
			} else {
				reject(new Error("유튜브 플레이어 API 가 준비되지 않았습니다"));
			}
		};
		// 스크립트가 이미 문서에 있으면 넣지 않는다 — 두 번 넣으면 콜백이 두 번 불린다.
		if (document.querySelector(`script[src="${SCRIPT_SRC}"]`)) {
			return;
		}
		const script = document.createElement("script");
		script.src = SCRIPT_SRC;
		script.async = true;
		script.onerror = () => reject(new Error("유튜브 플레이어 API 를 불러오지 못했습니다"));
		document.head.appendChild(script);
	});
	// 실패한 약속을 캐시에 남기면 다시 시도할 길이 없어진다(네트워크가 잠깐 끊긴 경우가 그렇다).
	pending = loading.catch((error) => {
		pending = null;
		throw error;
	});
	return pending;
}
