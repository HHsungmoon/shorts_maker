import type { ShortsMediaList } from "../types";

interface Props {
	media: ShortsMediaList;
	selectedSourceId: number | null;
	onSelect: (sourceId: number) => void;
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

export function MediaLibrary({ media, selectedSourceId, onSelect, onDelete, busy }: Props) {
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
					{item.sourceId !== null && item.sourceId === selectedSourceId && (
						<span className="sm-score">선택됨</span>
					)}
					<span className="sm-actions sm-actions--end">
						{item.sourceId !== null && item.sourceId !== selectedSourceId && (
							<button
								type="button"
								className="button button--small"
								onClick={() => onSelect(item.sourceId as number)}
							>
								선택
							</button>
						)}
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
