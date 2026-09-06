import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAsync } from "../shared/useAsync";
import { clipFileUrl, fetchWatchSource, postQuestion, recordEvent, toggleLike } from "./api";
import type { WatchClip, WatchQuestion, WatchUnanswerable } from "./api";

const MAX_LENGTH = 200;

function ClipCard({ clip, sourceId }: { clip: WatchClip; sourceId: number }) {
	return (
		<li className="watch-clip">
			{/* preload="metadata" — 한 줄에 카드가 여럿이라 자동 재생분까지 받아오면 첫 화면이 느려진다.
			    첫 프레임(포스터)조차 없이 검은 칸만 보이는 것보다는 메타데이터까지가 낫다. */}
			<video
				className="watch-clip__video"
				src={clipFileUrl(clip.id)}
				controls
				preload="metadata"
				playsInline
				onPlay={() => recordEvent("short_play", sourceId, { clipId: clip.id })}
				onEnded={() => recordEvent("short_complete", sourceId, { clipId: clip.id })}
			/>
			{clip.question && <p className="watch-clip__question">{clip.question}</p>}
			<p className="watch-clip__meta">
				{/* 1명이면 "1명이 물어봤어요"가 오히려 초라하다 — 여럿일 때만 말한다. */}
				{clip.asked_by > 1 && <span>{clip.asked_by}명이 물어봤어요</span>}
				{clip.total_sec ? <span>{Math.round(clip.total_sec)}초</span> : null}
			</p>
		</li>
	);
}

function UnanswerableRow({ item }: { item: WatchUnanswerable }) {
	return (
		<li className="watch-missing">
			<p className="watch-missing__question">{item.question}</p>
			<p className="watch-missing__note">
				{item.suggested_source_id !== null && item.suggested_title ? (
					<>
						이 영상엔 없어요 ·{" "}
						<Link to={`/watch/${item.suggested_source_id}`} className="watch-missing__link">
							“{item.suggested_title}”
						</Link>{" "}
						편에서 다룹니다
					</>
				) : (
					"이 영상에서는 답을 찾지 못했어요"
				)}
			</p>
		</li>
	);
}

function QuestionRow({
	question,
	onToggle,
	busy,
}: {
	question: WatchQuestion;
	onToggle: (id: number) => void;
	busy: boolean;
}) {
	return (
		<li className="watch-question">
			<button
				type="button"
				className={`watch-like${question.liked_by_me ? " watch-like--on" : ""}`}
				onClick={() => onToggle(question.id)}
				disabled={busy}
				aria-pressed={question.liked_by_me}
				aria-label={question.liked_by_me ? "좋아요 취소" : "좋아요"}
			>
				<span aria-hidden="true">♥</span>
				<span className="watch-like__count">{question.likes}</span>
			</button>
			<p className="watch-question__text">{question.text}</p>
		</li>
	);
}

export function SourcePage() {
	const params = useParams();
	const sourceId = Number(params.sourceId);
	// 🔴 key 로 remount 시킨다. 영상을 옮기면 지역 상태(쓰던 글·방금 누른 좋아요)가 자동으로
	// 비워진다 — 효과(useEffect)로 초기화하면 한 프레임 동안 옛 영상의 질문이 새 영상 밑에 보인다.
	return <SourceView key={sourceId} sourceId={sourceId} />;
}

function SourceView({ sourceId }: { sourceId: number }) {
	const detail = useAsync(() => fetchWatchSource(sourceId), [sourceId]);

	// 서버 목록이 정본이고, 이번 방문에 내가 한 것만 위에 덧씌운다. 상태로 복사하지 않는 이유는
	// 매번 전체를 다시 읽으면 화면이 통째로 깜빡이고(useAsync 주석) 방금 쓴 글이 사라진 것처럼
	// 보이기 때문이다.
	const [added, setAdded] = useState<WatchQuestion[]>([]);
	const [likeOverrides, setLikeOverrides] = useState<Record<number, { likes: number; liked: boolean }>>({});
	const [text, setText] = useState("");
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const submit = useCallback(
		async (event: React.FormEvent) => {
			event.preventDefault();
			const trimmed = text.trim();
			if (!trimmed || busy) {
				return;
			}
			setBusy(true);
			setError(null);
			try {
				const { questionId } = await postQuestion(sourceId, trimmed);
				// 서버 응답에는 id 만 있다. 나머지는 아는 값으로 채운다 — 좋아요가 0이라 정렬상
				// 맨 뒤지만, 방금 쓴 글은 눈에 보여야 한다.
				setAdded((current) => [
					{
						id: questionId,
						text: trimmed,
						cluster_id: null,
						created_at: new Date().toISOString(),
						likes: 0,
						liked_by_me: false,
					},
					...current,
				]);
				setText("");
			} catch (e: unknown) {
				// 레이트리밋(429)과 길이 초과(422)는 서버가 이유를 문장으로 준다.
				setError(e instanceof Error ? e.message : String(e));
			} finally {
				setBusy(false);
			}
		},
		[busy, sourceId, text],
	);

	const like = useCallback(async (questionId: number) => {
		setError(null);
		try {
			const result = await toggleLike(questionId);
			setLikeOverrides((current) => ({
				...current,
				[questionId]: { likes: result.likes, liked: result.liked },
			}));
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		}
	}, []);

	if (detail.loading) {
		return <p className="state">불러오는 중…</p>;
	}
	if (detail.error || !detail.data) {
		return (
			<div className="state state--error">
				<p>영상을 찾을 수 없습니다.</p>
				<Link to="/watch" className="watch-back">
					목록으로
				</Link>
			</div>
		);
	}

	const { source, clips, unanswerable } = detail.data;
	const remaining = MAX_LENGTH - text.length;
	const questions = [...added, ...detail.data.questions].map((question) => {
		const mine = likeOverrides[question.id];
		return mine ? { ...question, likes: mine.likes, liked_by_me: mine.liked } : question;
	});

	return (
		<article className="watch-detail">
			<Link to="/watch" className="watch-back">
				← 목록으로
			</Link>

			{source.youtube_id ? (
				<div className="watch-player">
					{/* 자체 <video> 가 아니라 임베드다 — 여기서의 시청이 실제 유튜브 시청 시간이 된다(tease §8-3). */}
					<iframe
						src={`https://www.youtube.com/embed/${source.youtube_id}`}
						title={source.title}
						allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
						allowFullScreen
					/>
				</div>
			) : (
				<p className="state">이 영상은 임베드 재생을 지원하지 않습니다.</p>
			)}

			<h1 className="watch-title">{source.title}</h1>
			{source.channel && <p className="watch-channel">{source.channel}</p>}
			{source.youtube_id && (
				<a
					className="watch-origin"
					href={`https://www.youtube.com/watch?v=${source.youtube_id}`}
					target="_blank"
					rel="noreferrer"
					onClick={() => recordEvent("cta_click", sourceId, { from: "source_page" })}
				>
					유튜브에서 보기
				</a>
			)}

			{/* 이 제품의 값이 여기서 갚아진다 — "물어봤더니 답이 왔다". 그래서 질문 폼보다 위다.
			    발행된 클립이 없으면 빈 껍데기를 두지 않고 통째로 감춘다. */}
			{clips.length > 0 && (
				<section>
					<h2 className="watch-section">
						질문에 대한 답 <span className="watch-count">{clips.length}</span>
					</h2>
					{/* 9:16 세로 영상이라 세로로 쌓으면 한 화면에 한 개도 안 들어간다. 가로 스크롤. */}
					<ul className="watch-clips">
						{clips.map((clip) => (
							<ClipCard key={clip.id} clip={clip} sourceId={sourceId} />
						))}
					</ul>
				</section>
			)}

			<section className="watch-ask">
				<h2 className="watch-section">궁금한 걸 남겨 주세요</h2>
				<p className="watch-hint">
					비슷한 질문이 모이면 크리에이터가 그 답만 잘라 숏폼으로 만듭니다. 로그인은 없습니다.
				</p>
				<form onSubmit={submit} className="watch-form">
					<textarea
						className="watch-input"
						value={text}
						onChange={(event) => setText(event.target.value.slice(0, MAX_LENGTH))}
						placeholder="예) 연봉 협상은 언제 꺼내는 게 좋나요?"
						rows={3}
						maxLength={MAX_LENGTH}
					/>
					<div className="watch-form__foot">
						<span className={`watch-counter${remaining < 20 ? " watch-counter--low" : ""}`}>
							{remaining}자 남음
						</span>
						<button
							type="submit"
							className="button button--primary"
							disabled={busy || text.trim().length === 0}
						>
							{busy ? "보내는 중…" : "질문 남기기"}
						</button>
					</div>
				</form>
				{error && <p className="state state--error">{error}</p>}
			</section>

			<section>
				<h2 className="watch-section">
					다른 사람들의 질문 <span className="watch-count">{questions.length}</span>
				</h2>
				{questions.length === 0 ? (
					<p className="state">첫 질문을 남겨 보세요.</p>
				) : (
					<ul className="watch-questions">
						{questions.map((question) => (
							<QuestionRow key={question.id} question={question} onToggle={like} busy={busy} />
						))}
					</ul>
				)}
			</section>

			{/* 크리에이터가 "이 영상엔 답이 없다"고 판정한 질문. 실패 통보가 아니라 안내라서
			    조용히 둔다 — 질문 목록 아래, 흐린 글씨로. */}
			{unanswerable.length > 0 && (
				<section>
					<h2 className="watch-section">여기서는 답하지 못한 질문</h2>
					<ul className="watch-missings">
						{unanswerable.map((item) => (
							<UnanswerableRow key={item.id} item={item} />
						))}
					</ul>
				</section>
			)}
		</article>
	);
}
