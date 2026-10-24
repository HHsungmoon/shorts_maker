import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { GutterPicker, ThemePicker } from "./ThemePicker";
import { LOGO_SRC, PRODUCT_NAME } from "../../shared/brand";
import { time } from "../../shared/format";
import type { ShortsSourceDetail } from "../types";

/** 미니 플레이어가 지금 어디서부터 틀고 있나. null 이면 닫혀 있다. */
export interface MiniPlayer {
	start: number;
	/** 같은 시각을 다시 눌러도 처음부터 다시 틀게 iframe 을 새로 그리는 열쇠. */
	nonce: number;
}

interface SourceBarProps {
	sourceId: number;
	data: ShortsSourceDetail | null;
	utterances: number;
	segments: number;
	prepDone: boolean;
	/** 답을 기다리는 질문 수. 질문 탭에 갈 이유가 곧 이 숫자다. */
	openCount: number;
	/** 이 영상에 답이 없는 질문 수. 리포트가 가져갈 것. */
	missingCount: number;
	onPlay: (sec: number) => void;
}

const tabClass = ({ isActive }: { isActive: boolean }) => `sm-bar__tab${isActive ? " sm-bar__tab--on" : ""}`;

/**
 * 영상 화면의 상단바 — 무엇을 작업 중인지 · 어디로 갈지.
 *
 * 🔴 예전에는 펼친 플레이어가 **모든 탭**의 맨 위에 있어서 세로 470px 를 먹었고, 탭 줄이 y≈700 에야
 * 나왔다. 1440×900 화면에서 첫 화면에 일할 자리가 거의 없었다(2026-10-21 개선안). 상단바 세 줄
 * 중 탭 줄만 화면에 붙어서, 스크롤해도 탭과 "원본 보기" 가 사라지지 않는다.
 * 왼쪽 레일(A1)로 먼저 만들었다가 이쪽(A2)으로 바꿨다(2026-10-24 사용자 확인) — 레일이 가로 296px 를
 * 늘 먹어서 질문 3단이 좁아졌다. 원본은 필요할 때만 오른쪽 아래 미니 플레이어로 띄운다(SourcePage).
 */
export function SourceBar({
	sourceId,
	data,
	utterances,
	segments,
	prepDone,
	openCount,
	missingCount,
	onPlay,
}: SourceBarProps) {
	const { authRequired, canAct, signOut } = useAuth();
	const source = data?.source ?? null;

	return (
		<>
			<header className="sm-bar">
				{/* 첫 줄은 제품, 둘째 줄은 지금 작업 중인 영상이다. 둘을 한 줄에 두었더니 로고와 긴 강연 제목이
				    붙어서 어디까지가 제품 이름인지 읽히지 않았다(2026-10-24 사용자 피드백). */}
				<div className="sm-bar__brand">
					{/* 로고는 첫 화면(`/`)으로 간다. 영상 목록은 바로 아래 ← 가 맡는다. */}
					<Link to="/" className="sm-bar__home" title="첫 화면">
						<img src={LOGO_SRC} alt={`${PRODUCT_NAME} 첫 화면`} />
					</Link>
					<span className="sm-bar__account">
						<GutterPicker />
						<ThemePicker />
						{authRequired && (
							<button type="button" className="button button--small" onClick={signOut}>
								로그아웃
							</button>
						)}
					</span>
				</div>

				<div className="sm-bar__row">
					{/* 링크로 열린 화면에는 브라우저 뒤로가기에 돌아갈 데가 없다. */}
					<Link to="/studio" className="sm-bar__back" aria-label="영상 목록으로" title="영상 목록">
						<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
							<path d="M15 18l-6-6 6-6" />
						</svg>
					</Link>
					<h1 className="sm-bar__title" title={source?.title}>
						{source ? source.title : "불러오는 중…"}
					</h1>
					{data && (
						<span className="sm-bar__meta">
							{source?.duration_sec != null && `${time(source.duration_sec)} · `}발화 {utterances} · 구간{" "}
							{segments} · 클립 {data.clips.length}
						</span>
					)}
					{source && (
						<span className={`sm-bar__chip${source.published ? " sm-bar__chip--ok" : ""}`}>
							{source.published ? "공개 중" : "비공개"}
						</span>
					)}
				</div>
			</header>

			{/* 🔴 to 는 절대경로다. 이 화면 자체가 splat 라우트(`sources/:sourceId/*`) 안이라
			    상대경로는 splat 구간까지 붙는다 — /sources/3/clips 에서 to="questions" 는
			    /sources/3/clips/questions 가 된다(react-router 7 의 v7_relativeSplatPath 기본값). */}
			{/* 🔴 순서가 곧 일하는 순서다 — 질문을 보고, 영상을 준비하고, 새로 뽑고, 내보낸다(2026-09-14).
			    공개 탭의 주소는 예전 클립 탭의 `clips` 를 그대로 쓴다 — 옛 링크가 같은 내용을 연다. */}
			<nav className="sm-bar__tabs" aria-label="영상 작업">
				<NavLink to={`/studio/sources/${sourceId}/questions`} className={tabClass}>
					질문
					{openCount > 0 && <span className="sm-bar__badge">{openCount}</span>}
				</NavLink>
				<NavLink to={`/studio/sources/${sourceId}/prepare`} className={tabClass}>
					영상 준비
					{/* 준비가 안 끝나면 질문에 답할 수 없다. 고장이 아니라 아직 안 한 일이라 알림 색이다. */}
					{data && !prepDone && <span className="sm-bar__need">준비 필요</span>}
				</NavLink>
				<NavLink to={`/studio/sources/${sourceId}/make`} className={tabClass}>
					새 클립
				</NavLink>
				<NavLink to={`/studio/sources/${sourceId}/clips`} className={tabClass}>
					공개
				</NavLink>
				{/* 🔴 리포트는 **가져가는 것**이고 기록은 내부용이다. 앞에 둔다. */}
				<NavLink to={`/studio/sources/${sourceId}/report`} className={tabClass}>
					리포트
					{missingCount > 0 && <span className="sm-bar__badge">{missingCount}</span>}
				</NavLink>
				<NavLink to={`/studio/sources/${sourceId}/log`} className={tabClass}>
					기록
				</NavLink>

				<span className="sm-bar__end">
					{/* 자체 <video> 가 아니라 유튜브 임베드다 — 여기서의 시청도 실제 유튜브 시청 시간이 된다(tease §8-3). */}
					{source?.youtube_id && (
						<button type="button" className="button button--small" onClick={() => onPlay(0)}>
							▶ 원본 보기
						</button>
					)}
					{/* 🔴 보기 전용 표시는 늘 보이는 자리에 둔다. 접어 두면 "왜 버튼이 안 먹지" 가 되고, 그게
					    심사 중에 일어나면 제품이 고장 난 것으로 보인다. 탭 줄은 화면에 붙어 있어서 스크롤해도
					    사라지지 않는다. 막힌 버튼을 누르면 이유는 토스트가 말한다(DeniedToast). */}
					{!canAct && (
						<span
							className="sm-bar__readonly"
							title="화면은 전부 볼 수 있고 실행·발행·수정만 막혀 있습니다"
						>
							보기 전용
						</span>
					)}
				</span>
			</nav>
		</>
	);
}

/**
 * 오른쪽 아래에 떠 있는 원본 플레이어. 본문을 밀어내지 않고, 탭을 옮겨도 그대로 튼다 —
 * 후보 대사를 읽다가 원본 위치를 누르면 여기서 그 초부터 나온다.
 */
export function MiniPlayerDock({
	youtubeId,
	title,
	player,
	onClose,
}: {
	youtubeId: string;
	title: string;
	player: MiniPlayer;
	onClose: () => void;
}) {
	return (
		<aside className="sm-mini" aria-label="원본 플레이어">
			<iframe
				key={player.nonce}
				src={`https://www.youtube.com/embed/${youtubeId}?start=${player.start}&autoplay=1`}
				title={title}
				allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
				allowFullScreen
			/>
			<div className="sm-mini__bar">
				<span>원본 · {time(player.start)} 부터</span>
				<button type="button" className="sm-mini__close" aria-label="원본 플레이어 닫기" onClick={onClose}>
					×
				</button>
			</div>
		</aside>
	);
}
