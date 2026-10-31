import { useState } from "react";
import { chooseTranscript, fetchTranscriptDiff } from "../api";
import { time } from "../../shared/format";
import { useAsync } from "../../shared/useAsync";
import type { TranscriptDiff, TranscriptOrigin, TranscriptSpot, TranscriptSpotKind } from "../types";

/** 서버(transcript_diff.KINDS)와 같은 순서. 🔴 "지어냈을 수 있음" 이 맨 앞이다 — 뜻을 바꾸는 것이 그것이다. */
const KINDS: { key: TranscriptSpotKind; label: string; hint: string }[] = [
	{
		key: "invented",
		label: "지어냈을 수 있음",
		hint: "음성 인식 쪽에 소리에 붙지 않은(0초) 단어가 있습니다. 없는 말을 만들었을 수 있어 꼭 들어 봐야 합니다",
	},
	{ key: "caption_missing", label: "자막이 빠뜨림", hint: "음성 인식에만 있는 말입니다" },
	{ key: "whisper_missing", label: "음성 인식이 빠뜨림", hint: "자막에만 있는 말입니다. 군말일 때가 많습니다" },
	{ key: "different", label: "다르게 들음", hint: "같은 자리를 다르게 받아 적었습니다" },
	{ key: "number", label: "숫자 표기", hint: "한쪽은 숫자, 한쪽은 글자로 적었습니다. 말은 같습니다" },
];

const LABEL: Record<TranscriptOrigin, string> = { youtube: "유튜브 자막", whisper: "음성 인식" };

/**
 * 자막 ↔ 음성 인식 비교(tease §5-1, 2026-11-01). 영상 준비 탭에서 두 대사가 다 있을 때만 보인다.
 *
 * 다른 곳마다 시각 · 자막 쪽 · 음성 인식 쪽을 나란히 두고, 시각을 누르면 오른쪽 아래 플레이어가 그 몇 초
 * 앞부터 튼다 — 어느 쪽이 맞는지는 **들어 봐야** 안다. 다 본 뒤 대사 전체를 어느 쪽으로 쓸지 고른다.
 * TODO: 다른 곳 하나하나에서 쪽을 고르기 · 교정 사전에 바로 넣기.
 */
export function TranscriptCompare({
	sourceId,
	using,
	version,
	busy,
	act,
	playAt,
}: {
	sourceId: number;
	/** 지금 쓰는 대사. */
	using: TranscriptOrigin | null;
	/** 바뀌면 다시 계산한다 — 두 대사의 발화 수 · 교정 사전. */
	version: string;
	busy: boolean;
	act: (run: () => Promise<unknown>) => void;
	playAt: (sec: number) => void;
}) {
	const diff = useAsync<TranscriptDiff>(() => fetchTranscriptDiff(sourceId), [sourceId, version], {
		keepPrevious: true,
		subject: sourceId,
	});
	const [picked, setPicked] = useState<TranscriptSpotKind | null>(null);

	if (diff.loading) return <p className="sm-meta">두 대사를 맞대는 중…</p>;
	if (diff.error) return <p className="sm-prep__warn">비교를 불러오지 못했습니다: {diff.error.message}</p>;
	const data = diff.data;
	if (!data) return null;
	if (!data.available) return <p className="sm-meta">{data.reason}</p>;

	// 처음에는 "지어냈을 수 있음" 을 연다. 없으면 차이가 있는 첫 종류.
	const kind = picked ?? KINDS.find((k) => data.byKind[k.key] > 0)?.key ?? "invented";
	const spots = data.spots.filter((s) => s.kind === kind);
	const hint = KINDS.find((k) => k.key === kind)?.hint;

	return (
		<div className="sm-tcmp">
			<div className="sm-tcmp__head">
				<p className="sm-tcmp__summary">
					음성 인식이 들은 {time(data.comparedSec)} 안에서 맞댔습니다 · 글자{" "}
					{(data.mismatchRatio * 100).toFixed(1)}% 다름 · 다른 곳 {data.spots.length}
				</p>
				{/* 대사 전체를 어느 쪽으로 쓸지. 다시 받거나 다시 듣지 않는다 — 보관한 것을 옮겨 담는다.
				    🔴 구간·클립의 발화 번호는 서버가 시각으로 다시 맞춘다(transcripts.use). */}
				<span className="sm-actions sm-actions--end">
					<span className="sm-meta">쓰는 대사</span>
					{(["youtube", "whisper"] as const).map((origin) => (
						<button
							key={origin}
							type="button"
							className={`sm-chip${using === origin ? " sm-chip--on" : ""}`}
							aria-pressed={using === origin}
							disabled={busy}
							onClick={() => {
								if (using !== origin) act(() => chooseTranscript(sourceId, origin));
							}}
						>
							{using === origin ? "✓ " : ""}
							{LABEL[origin]}
						</button>
					))}
				</span>
			</div>

			<div className="sm-tcmp__kinds" role="tablist">
				{KINDS.map((k) => (
					<button
						key={k.key}
						type="button"
						role="tab"
						aria-selected={kind === k.key}
						className={`sm-chip${kind === k.key ? " sm-chip--on" : ""}${k.key === "invented" && data.byKind.invented > 0 ? " sm-tcmp__danger" : ""}`}
						onClick={() => setPicked(k.key)}
					>
						{k.label} <span className="sm-chip__n">{data.byKind[k.key]}</span>
					</button>
				))}
			</div>
			{hint && <p className="sm-meta sm-tcmp__hint">{hint}</p>}

			{spots.length === 0 ? (
				<p className="sm-meta">여기에 해당하는 곳이 없습니다</p>
			) : (
				<ul className="sm-tcmp__list">
					{spots.map((spot) => (
						<SpotLine key={`${spot.start_sec}-${spot.kind}`} spot={spot} playAt={playAt} />
					))}
				</ul>
			)}
		</div>
	);
}

/** 다른 곳 한 줄. 앞뒤 문맥 사이에 다른 부분을 강조한다. 빈 쪽은 "(없음)". */
function SpotLine({ spot, playAt }: { spot: TranscriptSpot; playAt: (sec: number) => void }) {
	return (
		<li className="sm-tcmp__item">
			{/* 3초 앞부터 — 바로 그 초에서 틀면 앞 말을 못 들어 어디인지 가늠이 안 된다. */}
			<button
				type="button"
				className="sm-linkbtn sm-tcmp__time"
				onClick={() => playAt(Math.max(0, spot.start_sec - 3))}
				title="이 자리 들어 보기"
			>
				▶ {time(spot.start_sec)}
			</button>
			<div className="sm-tcmp__pair">
				<p>
					<span className="sm-tcmp__who">자막</span>
					<Context before={spot.before} focus={spot.caption} after={spot.after} />
				</p>
				<p>
					<span className="sm-tcmp__who">음성 인식</span>
					<Context before={spot.before} focus={spot.whisper} after={spot.after} />
				</p>
			</div>
		</li>
	);
}

function Context({ before, focus, after }: { before: string; focus: string; after: string }) {
	return (
		<span className="sm-tcmp__text">
			{before && <span className="sm-meta">…{before} </span>}
			{focus ? <mark>{focus}</mark> : <mark className="sm-tcmp__none">(없음)</mark>}
			{after && <span className="sm-meta"> {after}…</span>}
		</span>
	);
}
