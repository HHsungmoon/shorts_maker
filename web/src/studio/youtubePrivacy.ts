// 유튜브 공개 범위의 화면 이름. 업로드 대화상자와 공개 탭 카드의 딱지가 같이 쓴다 —
// 컴포넌트 파일에서 상수를 내보내면 Fast Refresh 가 깨져(lint only-export-components) 따로 둔다.
import type { YoutubePrivacy } from "./types";

export const PRIVACY_LABEL: Record<YoutubePrivacy, string> = {
	private: "비공개",
	unlisted: "일부 공개",
	public: "공개",
};
