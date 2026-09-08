import type { ShortsMediaList } from "../types";

// 디스크에 실제로 있는 파일 목록이다 — 등록된 영상 목록(홈의 카드)과 겹치지만 같지 않다.
// 여기에만 보이는 것이 둘 있다: 아직 등록하지 않은 파일(scp 로 올린 것)과 용량.
//
// 🔴 영상을 고르는 일은 이제 홈의 카드가 한다. 선택 버튼을 여기 두면 같은 일을 하는 길이 둘이
// 되고, 고른 결과가 URL 에 남지 않는다.
interface Props {
	media: ShortsMediaList;
	onDelete: (name: string) => void;
	busy: boolean;
}

function size(bytes: number): string {
	if (bytes >= 1 << 30) {
		return `${(bytes / (1 << 30)).toFixed(1)}GB`;
	}
	return `${Math.round(bytes / (1 << 20))}MB`;
}

function minutes(seconds: number | null): string {
	return seconds ? `${Math.round(seconds / 60)}분` : "-";
}

export function MediaLibrary({ media, onDelete, busy }: Props) {
	const { disk } = media;
	const usedPct = Math.round((disk.usedBytes / disk.totalBytes) * 100);

	return (
		<section className="sm-panel">
			<div className="sm-actions">
				<strong>저장된 영상 {media.items.length}개</strong>
				<span className="sm-actions--end sm-meta">
					디스크 {size(disk.totalBytes)} 중 {size(disk.usedBytes)} 사용 ({usedPct}%) · 남은 공간{" "}
					{size(disk.freeBytes)} · 영상 {size(disk.sourcesBytes)} · 작업파일{" "}
					{size(disk.workBytes)}
				</span>
			</div>

			{media.items.length === 0 && (
				<p className="state">아직 없습니다. 오른쪽 위 “새로 만들기”로 추가하세요.</p>
			)}

			{media.items.map((item) => (
				<div
					key={item.name}
					className="sm-actions"
					style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border)" }}
				>
					<span>{item.title ?? item.name}</span>
					<span className="sm-meta">
						{size(item.sizeBytes)} · {minutes(item.durationSec)} · {item.name}
					</span>
					{item.sourceId === null && <span className="sm-badge">미등록</span>}
					<span className="sm-actions sm-actions--end">
						<button
							type="button"
							className="button button--small button--danger"
							disabled={busy}
							onClick={() => onDelete(item.name)}
						>
							삭제
						</button>
					</span>
				</div>
			))}
		</section>
	);
}
