import { useCallback, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useAsync } from "../shared/useAsync";
import { clipFileUrl, fetchWatchSource, postQuestion, recordEvent, toggleLike } from "./api";
import type { WatchClip, WatchQuestion, WatchUnanswerable } from "./api";
import { ClipModal } from "./ClipModal";
import { OriginPlayer } from "./OriginPlayer";
import type { OriginPlayerHandle } from "./OriginPlayer";

const MAX_LENGTH = 200;

function ClipCard({ clip, onOpen }: { clip: WatchClip; onOpen: (clip: WatchClip) => void }) {
	return (
		<li className="watch-clip">
			{/* 🔴 카드 안에서 재생하지 않는다(2026-09-20). 한 줄에 둘이라 폭이 170px 남짓인데
			    거기서 9:16 을 틀면 **자막이 안 읽힌다** — 자막이 이 클립 내용의 절반이다.
			    카드는 고르는 자리고, 보는 자리는 가운데 모달이다(ClipModal).

			    그래서 controls 를 떼고 카드 전체를 버튼으로 만든다. muted 는 자동재생 방지용이
			    아니라 혹시라도 소리가 나지 않게 하는 보험이다. */}
			<button type="button" className="watch-clip__thumb" onClick={() => onOpen(clip)}>
				<video
					className="watch-clip__video"
					src={clipFileUrl(clip.id)}
					preload="metadata"
					muted
					playsInline
					tabIndex={-1}
				/>
				<span className="watch-clip__play" aria-hidden="true">
					▶
				</span>
				<span className="sr-only">재생: {clip.title ?? clip.question ?? "숏폼"}</span>
			</button>
			<div className="watch-clip__body">
				{/* title 속성: 한 줄로 잘린 제목의 전문을 마우스를 올리면 보여준다(watch.css). */}
				{clip.title && (
					<p className="watch-clip__question" title={clip.title}>
						{clip.title}
					</p>
				)}
				<p className="watch-clip__meta">
					{/* 🔴 질문에서 나온 숏폼만 숫자를 붙인다. 크리에이터가 직접 뽑은 것에 붙이면
					    거짓말이 된다. 1명이면 오히려 초라해서 여럿일 때만 말한다.
					    길이(초)는 빼 뒀다 — 30초 예산 안이라 그 숫자로 고를 일이 없다.

					    🔴 **목록이 좋아요 합산 순으로 정렬된다**(watch.py). 그래서 좋아요가 있으면
					    그 숫자를 먼저 보여준다 — 정렬 기준과 다른 숫자를 띄우면 "4명" 이 "8명" 위에
					    오는 화면이 되어 순서가 고장난 것처럼 읽힌다. */}
					{clip.question && clip.liked_by > 0 && <span>{clip.liked_by}명이 궁금해했어요</span>}
					{clip.question && clip.liked_by === 0 && clip.asked_by > 1 && (
						<span>{clip.asked_by}명이 물어봤어요</span>
					)}
				</p>
				{/* 🔴 **원본으로 가는 길은 모달 안**으로 옮겼다(2026-09-20). 답을 다 들은 자리에
				    놓는 것이 이 버튼의 뜻인데, 카드에서는 아직 아무것도 안 들은 상태다.
				    여기서는 "답을 보러 가는" 입구만 둔다. */}
				{/* 길이(초)는 붙이지 않는다 — 30초 예산 안이라 그 숫자로 고를 일이 없다(2026-09-17). */}
				<button type="button" className="watch-jump watch-jump--open" onClick={() => onOpen(clip)}>
					답변 보기
				</button>
			</div>
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
	answer,
	onToggle,
	onOpen,
	busy,
}: {
	question: WatchQuestion;
	/** 이 질문이 속한 묶음에 이미 발행된 숏폼. 없으면 아직 답이 안 나온 질문이다. */
	answer: WatchClip | undefined;
	onToggle: (id: number) => void;
	onOpen: (clip: WatchClip) => void;
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
			<div className="watch-question__body">
				<p className="watch-question__text">{question.text}</p>
				{/* 🔴 답이 이미 나와 있는데 목록에서는 알 길이 없었다(2026-09-20). 아래 숏폼 줄을
				    뒤져서 같은 질문을 찾아내라는 것은 시청자의 일이 아니다. 답이 있으면 그
				    자리에서 말하고, 누르면 바로 튼다. */}
				{answer && (
					<button
						type="button"
						className="watch-answer"
						onClick={() => onOpen(answer)}
					>
						답변 보기
					</button>
				)}
			</div>
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
	// 방금 남긴 질문과 비슷한 궁금증에 답한 발행 숏폼. 다음 질문을 보내면 비운다 — 옛 질문의 추천이
	// 새 질문 밑에 남으면 엉뚱한 답을 권하는 것이 된다.
	const [suggested, setSuggested] = useState<WatchClip[]>([]);
	// 가운데에서 크게 트는 숏폼. 카드·질문 줄·추천이 모두 이 하나를 연다.
	const [playing, setPlaying] = useState<WatchClip | null>(null);

	const submit = useCallback(
		async (event: React.FormEvent) => {
			event.preventDefault();
			const trimmed = text.trim();
			if (!trimmed || busy) {
				return;
			}
			setBusy(true);
			setError(null);
			setSuggested([]);
			try {
				const { questionId, suggestions } = await postQuestion(sourceId, trimmed);
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
				setSuggested(suggestions ?? []);
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

	// 🔴 이 아래 훅 셋은 이른 return(로딩·오류) **앞**에 있어야 한다 — 훅은 순서가 계약이라
	// 조건부 return 뒤에 두면 렌더마다 개수가 달라져 React 가 상태를 잘못 짚는다. 그래서
	// youtube_id 도 여기서 옵셔널 체이닝으로 미리 꺼낸다.
	const youtubeId = detail.data?.source.youtube_id ?? null;
	const playerRef = useRef<OriginPlayerHandle | null>(null);

	/**
	 * 숏폼 CTA — 원본의 그 초로 옮긴다. **이 함수가 이 제품의 주장을 실행한다.**
	 *
	 * 퍼널은 여기서 두 칸이 찍힌다: 누른 것(`cta_click`)과 실제로 옮겨진 것(`origin_seek`).
	 * 둘을 나눈 이유는 플레이어가 없어서 못 옮기는 경우를 구분해야 하기 때문이다 —
	 * 하나로 합치면 "눌렀는데 아무 일도 없었다" 가 성공으로 집계된다.
	 */
	const jump = useCallback(
		(clip: WatchClip) => {
			recordEvent("cta_click", sourceId, { clipId: clip.id });
			if (playerRef.current?.jumpTo(clip.start_sec, clip.id)) {
				recordEvent("origin_seek", sourceId, {
					clipId: clip.id,
					payload: { at: Math.round(clip.start_sec) },
				});
				return;
			}
			// 플레이어 API 가 막혔다. 유튜브를 그 초로 열어 주는 것이 아무것도 안 하는 것보다 낫다.
			// 🔴 이때 origin_seek 은 남기지 않는다 — 새 탭에서 실제로 봤는지 우리는 관측할 수
			// 없고, 관측할 수 없는 것을 퍼널에 넣으면 뒤 칸(origin_play)이 영원히 비어 보인다.
			if (youtubeId) {
				window.open(
					`https://www.youtube.com/watch?v=${youtubeId}&t=${Math.floor(clip.start_sec)}s`,
					"_blank",
					"noreferrer",
				);
			}
		},
		[sourceId, youtubeId],
	);

	// 퍼널의 마지막 칸. CTA 뒤 30초 안에 원본 재생이 시작됐다는 뜻이다(OriginPlayer 의 창).
	const originPlay = useCallback(
		(clipId: number) => recordEvent("origin_play", sourceId, { clipId }),
		[sourceId],
	);

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

	// 질문 → 그 묶음에 이미 발행된 숏폼. 질문 줄의 "답변 보기" 가 이걸로 뜬다.
	// 🔴 한 묶음에 클립이 여럿이면 목록 순서(수요 순)의 첫 번째를 쓴다 — 목록이 앞세운 것과
	// 질문 줄이 여는 것이 다르면 같은 화면이 서로 다른 말을 한다.
	const answerOf = new Map<number, WatchClip>();
	for (const clip of clips) {
		if (clip.question_cluster_id !== null && !answerOf.has(clip.question_cluster_id)) {
			answerOf.set(clip.question_cluster_id, clip);
		}
	}

	// 🔴 답변 숏폼을 **영상 오른쪽**에 둔다(유튜브의 관련 영상 자리). 본문 아래로 내리면 스크롤을
	// 해야 보이는데, 이 제품의 값이 갚아지는 자리가 거기다 — 영상을 보는 내내 눈에 있어야 한다.
	// 그 단 안에서 카드는 **가로 2개씩**이고 영상 아래에 제목과 CTA 가 온다(2026-09-17, watch.css).
	// 좁은 화면에서는 한 단으로 접히고 숏폼이 질문 폼보다 위로 온다.
	const shorts = (
		<section className="watch-shorts">
			{/* 🔴 "질문에 대한 답" 이라고 부르지 않는다. 이 목록에는 크리에이터가 자기 기준으로
			    뽑은 숏폼도 함께 올라간다 — 질문에서 나온 것만 있는 자리가 아니다. */}
			<h2 className="watch-section watch-section--tight">
				이 영상의 숏폼
				{clips.length > 0 && <span className="watch-count">{clips.length}</span>}
			</h2>
			{clips.length === 0 ? (
				// 🔴 빈 칸으로 두지 않는다. 2단 레이아웃에서 오른쪽이 비면 깨져 보이고, 무엇보다
				// 이 한 줄이 이 서비스가 무엇인지 설명한다 — 처음 온 사람이 질문을 남길 이유가 된다.
				<p className="watch-empty">
					아직 올라온 숏폼이 없어요. 궁금한 걸 남기면 그 답만 잘라 숏폼으로 만들어 여기에 올립니다.
				</p>
			) : (
				<ul className="watch-clips">
					{clips.map((clip) => (
						<ClipCard key={clip.id} clip={clip} onOpen={setPlaying} />
					))}
				</ul>
			)}
		</section>
	);

	return (
		<article className="watch-detail">
			<Link to="/watch" className="watch-back">
				← 목록으로
			</Link>

			<div className="watch-columns">
			<div className="watch-main">

			{source.youtube_id ? (
				// 자체 <video> 가 아니라 임베드다 — 여기서의 시청이 실제 유튜브 시청 시간이 된다(tease §8-3).
				// 🔴 평범한 <iframe> 이 아니라 IFrame Player API 다. 숏폼 CTA 가 이 플레이어를 그 초로
				// 움직여야 하고, 그건 API 없이는 불가능하다(OriginPlayer 머리 주석).
				<OriginPlayer
					ref={playerRef}
					youtubeId={source.youtube_id}
					title={source.title}
					onOriginPlay={originPlay}
				/>
			) : (
				<p className="state">이 영상은 임베드 재생을 지원하지 않습니다.</p>
			)}

			<h1 className="watch-title">{source.title}</h1>
			{source.channel && <p className="watch-channel">{source.channel}</p>}
			{source.youtube_id && (
				// 🔴 여기에는 `cta_click` 을 붙이지 않는다. 그 이벤트는 퍼널에서 "숏폼을 보고 원본으로
				// 가려 했다" 를 뜻하는데, 이 링크는 숏폼을 한 번도 안 본 사람도 누른다 — 섞으면
				// 재생·완주보다 CTA 가 많아지는 일이 생기고 퍼널이 거짓이 된다. 계측 없이 둔다.
				<a
					className="watch-origin"
					href={`https://www.youtube.com/watch?v=${source.youtube_id}`}
					target="_blank"
					rel="noreferrer"
				>
					유튜브에서 보기
				</a>
			)}

			{/* 좁은 화면에서만 여기 나온다 — 넓으면 오른쪽 단이 가져간다(watch.css). */}
			<div className="watch-shorts--inline">{shorts}</div>

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
							className="button button--primary watch-submit"
							disabled={busy || text.trim().length === 0}
						>
							{busy ? "보내는 중…" : "질문 남기기"}
						</button>
					</div>
				</form>
				{error && <p className="state state--error">{error}</p>}
				{/* 🔴 "답입니다" 가 아니라 "비슷한 궁금증에 답한 숏폼" 이다. 유사도로 고른 것이라 틀릴 수 있고,
				    단정했다가 틀리면 시청자는 속았다고 느낀다. 질문이 이미 남았다는 것도 같이 말한다 —
				    추천을 보고 "내 질문은 버려졌나" 로 읽히면 질문을 남기는 이유가 사라진다. */}
				{suggested.length > 0 && (
					<div className="watch-suggest" role="status">
						<div className="watch-suggest__head">
							<p className="watch-suggest__title">비슷한 궁금증에 답한 숏폼이 있어요</p>
							<button
								type="button"
								className="watch-suggest__close"
								onClick={() => setSuggested([])}
								aria-label="추천 닫기"
							>
								닫기
							</button>
						</div>
						<p className="watch-hint">
							질문은 그대로 남았어요. 찾던 답이 아니면 크리에이터가 비슷한 질문을 모아 따로 답합니다.
						</p>
						<ul className="watch-clips">
							{suggested.map((clip) => (
								<ClipCard key={clip.id} clip={clip} onOpen={setPlaying} />
							))}
						</ul>
					</div>
				)}
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
							<QuestionRow
								key={question.id}
								question={question}
								answer={question.cluster_id === null ? undefined : answerOf.get(question.cluster_id)}
								onToggle={like}
								onOpen={setPlaying}
								busy={busy}
							/>
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
			</div>

			{/* 넓은 화면의 오른쪽 단. 스크롤을 내려도 따라오게 sticky. */}
			<aside className="watch-side">{shorts}</aside>
			</div>

			{/* 가운데에서 크게 트는 자리. 카드·질문 줄·추천이 모두 이 하나를 연다. */}
			<ClipModal
				clip={playing}
				sourceId={sourceId}
				onClose={() => setPlaying(null)}
				onJump={jump}
			/>
		</article>
	);
}
