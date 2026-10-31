import { connectYoutube, disconnectYoutube } from "../api";
import { useAuth } from "../AuthContext";
import type { YoutubeState } from "../types";

/**
 * 유튜브 채널 연결 — 홈 머리의 버튼 하나(tease §7-5).
 *
 * 채널은 영상마다가 아니라 스튜디오 전체에 하나다(youtube_channel 은 한 행) — 그래서 영상 페이지가
 * 아니라 홈에 둔다. 연결은 이 창을 Google 동의 화면으로 옮기고, 끝나면 서버가 홈으로 돌려보낸다.
 */
export function YoutubeConnect({
	state,
	onError,
	onChanged,
}: {
	state: YoutubeState | null;
	onError: (message: string | null) => void;
	onChanged: (message: string) => void;
}) {
	// 🔴 연결·끊기는 useStudioJob 의 act 를 안 거치는 자리라 여기서 따로 막는다(StudioHome 의 삭제와 같다).
	const { canAct, refuse } = useAuth();
	if (!state) {
		return null;
	}
	const channel = state.channel;

	const connect = async () => {
		if (!canAct) {
			refuse();
			return;
		}
		onError(null);
		try {
			// 성공하면 이 창이 동의 화면으로 떠난다 — 돌아오는 건 StudioHome 의 ?youtube= 처리다.
			await connectYoutube();
		} catch (e: unknown) {
			onError(e instanceof Error ? e.message : String(e));
		}
	};

	const disconnect = async () => {
		if (!canAct) {
			refuse();
			return;
		}
		// 올린 영상은 채널에 그대로 남는다. 사라지는 건 "이 스튜디오가 그 채널에 올릴 수 있는 권한" 이다.
		if (!window.confirm("유튜브 채널 연결을 끊습니다.\n이미 올린 영상은 채널에 그대로 남습니다.")) {
			return;
		}
		onError(null);
		try {
			const result = await disconnectYoutube();
			// Google 쪽 거두기는 실패해도 우리 쪽은 지웠다. 남은 권한은 Google 계정 설정에서 지운다.
			onChanged(
				result.revoked
					? "유튜브 채널 연결을 끊었습니다."
					: "유튜브 채널 연결을 끊었습니다. Google 쪽 권한은 거두지 못했습니다 — myaccount.google.com 의 '서드 파티 앱' 에서 지우세요.",
			);
		} catch (e: unknown) {
			onError(e instanceof Error ? e.message : String(e));
		}
	};

	if (!channel) {
		return (
			<button
				type="button"
				className="button button--small"
				onClick={connect}
				title={state.configured ? "Google 동의 화면으로 이동합니다" : "서버 .env 에 유튜브 설정이 없습니다"}
			>
				유튜브 연결
			</button>
		);
	}
	return (
		<span className="sm-yt-channel">
			<span
				className="sm-yt-channel__name"
				title={channel.channel_title ? undefined : "채널 이름은 첫 업로드 뒤에 보입니다"}
			>
				▶ {channel.channel_title ?? "유튜브 연결됨"}
			</span>
			<button type="button" className="sm-yt-channel__off" onClick={disconnect}>
				끊기
			</button>
		</span>
	);
}
