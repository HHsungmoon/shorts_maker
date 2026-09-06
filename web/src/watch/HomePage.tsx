import { Link } from "react-router-dom";
import { useAsync } from "../shared/useAsync";
import { fetchWatchSources, thumbnailUrl } from "./api";
import type { WatchSource } from "./api";

function duration(seconds: number | null): string {
	if (!seconds) {
		return "";
	}
	const minutes = Math.round(seconds / 60);
	return minutes >= 60 ? `${Math.floor(minutes / 60)}시간 ${minutes % 60}분` : `${minutes}분`;
}

function Card({ source }: { source: WatchSource }) {
	return (
		<Link to={`/watch/${source.id}`} className="watch-card">
			<div className="watch-card__thumb">
				{source.youtube_id ? (
					// 로딩 실패해도 레이아웃이 무너지지 않게 배경색을 깔아 둔다.
					<img src={thumbnailUrl(source.youtube_id)} alt="" loading="lazy" />
				) : (
					<span className="watch-card__noimage">영상 미리보기 없음</span>
				)}
				{source.duration_sec ? (
					<span className="watch-card__duration">{duration(source.duration_sec)}</span>
				) : null}
			</div>
			<div className="watch-card__body">
				<h2 className="watch-card__title">{source.title}</h2>
				<p className="watch-card__meta">
					{source.channel ? <span>{source.channel}</span> : null}
					<span>질문 {source.question_count}</span>
					{source.published_clip_count > 0 && <span>숏폼 {source.published_clip_count}</span>}
				</p>
			</div>
		</Link>
	);
}

export function HomePage() {
	const sources = useAsync(fetchWatchSources, []);

	if (sources.loading) {
		return <p className="state">불러오는 중…</p>;
	}
	if (sources.error) {
		return <p className="state state--error">목록을 불러오지 못했습니다. {sources.error.message}</p>;
	}
	if (!sources.data || sources.data.length === 0) {
		// 크리에이터가 스튜디오에서 발행해야 여기 나타난다 — 시청자에게는 그 사정을 말하지 않는다.
		return <p className="state">아직 공개된 영상이 없습니다.</p>;
	}

	return (
		<div className="watch-grid">
			{sources.data.map((source) => (
				<Card key={source.id} source={source} />
			))}
		</div>
	);
}
