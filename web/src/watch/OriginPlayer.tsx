import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { loadYouTubeApi } from "./youtube";
import type { YouTubePlayer } from "./youtube";

// 🔴 CTA 를 누른 뒤 **이 시간 안에** 재생이 시작되면 "원본 유입" 으로 센다(update_plan D10).
// 창을 두는 이유: 시청자가 원래 보고 있던 재생을 유입으로 세면 지표가 거짓이 된다. 우리가
// 주장하려는 건 "숏폼이 원본을 보게 만들었다" 이고, 그 인과를 시간으로 좁히는 게 이 창이다.
const ORIGIN_WINDOW_MS = 30_000;

export interface OriginPlayerHandle {
	/**
	 * 그 초로 옮기고 재생한다.
	 *
	 * **false 를 돌려주면 플레이어가 없다** — API 스크립트가 막혔거나 아직 준비 전이다.
	 * 호출부는 그때 유튜브를 새 탭으로 여는 길로 물러난다(SourcePage). 조용히 아무 일도
	 * 안 일어나는 것이 가장 나쁘다.
	 */
	jumpTo(seconds: number, clipId: number): boolean;
}

interface Props {
	youtubeId: string;
	title: string;
	/** CTA 로 옮긴 뒤 창 안에서 재생이 시작됐다 — 이게 원본 유입 1건이다. */
	onOriginPlay: (clipId: number) => void;
}

/**
 * 원본 유튜브 플레이어. 숏폼의 CTA 가 이 플레이어를 그 초로 움직인다(tease §2-1, §8-3).
 *
 * DOM 을 다루는 방식에 주의가 하나 있다. `new YT.Player(el)` 은 넘긴 요소를 **iframe 으로
 * 교체한다** — React 가 그리는 노드를 넘기면 React 와 유튜브가 같은 자리를 두고 싸운다.
 * 그래서 컨테이너 안에 우리가 직접 만든 자식을 붙여 그걸 넘긴다. React 는 컨테이너만 소유한다.
 */
export const OriginPlayer = forwardRef<OriginPlayerHandle, Props>(function OriginPlayer(
	{ youtubeId, title, onOriginPlay },
	ref,
) {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const playerRef = useRef<YouTubePlayer | null>(null);
	// 마지막 CTA. 재생 시작이 이 뒤 30초 안이면 유입으로 센다.
	const seekRef = useRef<{ at: number; clipId: number } | null>(null);
	// 🔴 콜백을 ref 에 담는다. 아래 플레이어 생성 효과의 의존성에 넣으면 부모가 다시 그릴
	// 때마다 플레이어를 파괴하고 새로 만들어 재생이 끊긴다.
	//
	// 담는 것 자체도 효과 안에서 한다 — 렌더 중에 ref 를 쓰면 React 가 렌더를 버리고 다시
	// 돌릴 때(StrictMode·동시성) 순서가 보장되지 않는다. 이 값은 렌더가 끝난 뒤 유튜브
	// 콜백에서만 읽히므로 효과에서 넣어도 늦지 않다.
	const onOriginPlayRef = useRef(onOriginPlay);
	useEffect(() => {
		onOriginPlayRef.current = onOriginPlay;
	}, [onOriginPlay]);
	const [failed, setFailed] = useState(false);

	useEffect(() => {
		let cancelled = false;
		let player: YouTubePlayer | null = null;
		let host: HTMLDivElement | null = null;

		loadYouTubeApi()
			.then((api) => {
				if (cancelled || !containerRef.current) {
					return;
				}
				host = document.createElement("div");
				containerRef.current.appendChild(host);
				player = new api.Player(host, {
					videoId: youtubeId,
					// 🔴 nocookie 도메인(tease §8-3). 시청자가 재생을 누르기 전에 추적 쿠키를 심지 않는다.
					host: "https://www.youtube-nocookie.com",
					playerVars: {
						// 이게 없으면 seekTo 를 포함한 어떤 명령도 통하지 않는다.
						enablejsapi: 1,
						playsinline: 1,
						// 관련 영상을 같은 채널로 제한한다. 남의 영상으로 새 나가면 유입을 만든 의미가 없다.
						rel: 0,
						origin: window.location.origin,
					},
					events: {
						onStateChange: (event) => {
							if (event.data !== api.PlayerState.PLAYING) {
								return;
							}
							const seek = seekRef.current;
							if (!seek || Date.now() - seek.at > ORIGIN_WINDOW_MS) {
								return;
							}
							// 한 번만 센다. 창 안에서 멈추고 다시 틀면 유입 2건이 되어 버린다.
							seekRef.current = null;
							onOriginPlayRef.current(seek.clipId);
						},
					},
				});
				playerRef.current = player;
			})
			.catch(() => {
				// 스크립트가 막혔다(광고 차단기·회사망). 영상은 보여야 하므로 평범한 임베드로 물러난다.
				if (!cancelled) {
					setFailed(true);
				}
			});

		return () => {
			cancelled = true;
			playerRef.current = null;
			try {
				player?.destroy();
			} catch {
				// 이미 내려간 경우. 정리 중 예외로 화면을 깨뜨릴 이유가 없다.
			}
			host?.remove();
		};
	}, [youtubeId]);

	useImperativeHandle(
		ref,
		() => ({
			jumpTo(seconds: number, clipId: number) {
				const player = playerRef.current;
				if (!player) {
					return false;
				}
				seekRef.current = { at: Date.now(), clipId };
				player.seekTo(Math.max(0, seconds), true);
				// 🔴 CTA 클릭과 같은 호출 스택 안이라 브라우저가 자동재생으로 막지 않는다.
				// 사용자 제스처 없이 부르면 소리 있는 재생이 차단된다.
				player.playVideo();
				// 플레이어가 화면 밖일 수 있다 — 위치만 옮기고 보이지 않으면 아무 일도 안
				// 일어난 것처럼 보인다. 좁은 화면에서 숏폼 목록이 플레이어 아래에 오면 늘 그렇다.
				containerRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
				return true;
			},
		}),
		[],
	);

	if (failed) {
		return (
			<div className="watch-player">
				<iframe
					src={`https://www.youtube-nocookie.com/embed/${youtubeId}`}
					title={title}
					allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
					allowFullScreen
				/>
			</div>
		);
	}
	return <div className="watch-player" ref={containerRef} />;
});
