import { useState } from "react";
import { fetchReplyDraft, postReply } from "../api";
import { useAuth } from "../AuthContext";
import { useAsync } from "../../shared/useAsync";
import type { ReplyDraft, ShortsClusterQuestion } from "../types";

/**
 * 유튜브 댓글에 답글(tease §7-5, 2026-11-01). 질문 상세에서 **만든 숏폼 바로 아래**에 둔다 — 답글은 그 숏폼을
 * 가리키는 글이라, 숏폼을 보고 나서 바로 쓰는 자리여야 한다.
 *
 * 🔴 사람이 누를 때만 달린다. 초안은 채워 두지만 [답글 달기] 를 누르기 전에는 아무것도 나가지 않는다.
 * 🔴 보기 전용이어도 버튼을 잠그지 않는다 — 눌리면 `refuse()` 가 이유를 말한다(CLAUDE.md 인증 경계).
 */
export function CommentReplies({
	questions,
	onChanged,
}: {
	/** 이 묶음의 질문 중 유튜브 댓글에서 온 것. */
	questions: ShortsClusterQuestion[];
	/** 답글을 단 뒤 목록을 다시 읽는다 — 질문 줄의 "답글 달림" 이 서버 기록에서 온다. */
	onChanged: () => void;
}) {
	const [open, setOpen] = useState<number | null>(null);
	if (questions.length === 0) return null;
	return (
		<section className="sm-qd__section">
			<h4 className="sm-qd__label">유튜브 댓글에 답글</h4>
			<ul className="sm-replies">
				{questions.map((q) => (
					<li key={q.id} className="sm-replies__item">
						<div className="sm-replies__row">
							<span className="sm-replies__text">{q.text}</span>
							<ReplyState
								question={q}
								opened={open === q.id}
								onOpen={() => setOpen(open === q.id ? null : q.id)}
							/>
						</div>
						{open === q.id && (
							<ReplyComposer
								questionId={q.id}
								onCancel={() => setOpen(null)}
								onDone={() => {
									setOpen(null);
									onChanged();
								}}
							/>
						)}
					</li>
				))}
			</ul>
		</section>
	);
}

/** 이 댓글에 답글이 어떻게 됐나. 달렸으면 그 답글로 가는 주소, 아니면 입력란을 여는 버튼. */
function ReplyState({
	question,
	opened,
	onOpen,
}: {
	question: ShortsClusterQuestion;
	opened: boolean;
	onOpen: () => void;
}) {
	const reply = question.reply ?? null;
	if (reply?.status === "DONE" && reply.url) {
		return (
			<a className="sm-replies__done" href={reply.url} target="_blank" rel="noreferrer">
				답글 달림 ↗
			</a>
		);
	}
	if (reply?.status === "POSTING") {
		return <span className="sm-meta">답글 다는 중…</span>;
	}
	return (
		<button type="button" className="button button--small" aria-expanded={opened} onClick={onOpen}>
			{/* 실패한 답글은 다시 달 수 있다. 🔴 실제로는 달렸을 수 있어(응답만 잃음) 이유를 같이 보인다. */}
			{reply?.status === "FAILED" ? "다시 달기" : "답글"}
		</button>
	);
}

/** 초안을 채운 입력란. 누구 이름으로 달리는지와 못 다는 이유를 버튼 바로 위에 둔다. */
function ReplyComposer({
	questionId,
	onCancel,
	onDone,
}: {
	questionId: number;
	onCancel: () => void;
	onDone: () => void;
}) {
	const { canAct, refuse } = useAuth();
	const draft = useAsync<ReplyDraft>(() => fetchReplyDraft(questionId), [questionId]);
	// 사람이 고친 본문. 처음 열면 초안을 그대로 쓴다(null = 아직 안 고침).
	const [text, setText] = useState<string | null>(null);
	const [sending, setSending] = useState(false);
	const [error, setError] = useState<string | null>(null);

	if (draft.loading) return <p className="sm-meta">초안을 만드는 중…</p>;
	if (draft.error) return <p className="state state--error">{draft.error.message}</p>;
	const data = draft.data;
	if (!data) return null;

	const body = text ?? data.text;
	const channelName = data.channel?.channel_title ?? "연결된 채널";

	const send = async () => {
		if (!canAct) {
			refuse();
			return;
		}
		setSending(true);
		setError(null);
		try {
			await postReply(questionId, body);
			onDone();
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
			setSending(false);
		}
	};

	return (
		<div className="sm-replies__composer">
			{data.reply?.status === "FAILED" && data.reply.error && (
				<p className="sm-replies__warn">
					지난번 답글이 실패했습니다: {data.reply.error}. 유튜브에서 실제로 달렸는지 먼저 확인하세요.
				</p>
			)}
			{data.blocked ? (
				<p className="sm-replies__warn">{data.blocked}</p>
			) : (
				<>
					<textarea
						className="field__input sm-replies__input"
						value={body}
						onChange={(e) => setText(e.target.value)}
						rows={4}
						aria-label="답글"
					/>
					<p className="sm-meta">
						<strong>{channelName}</strong> 이름으로 공개 답글이 달립니다. 같은 영상의 댓글에 쓴 12:34 같은
						시각은 유튜브가 그 초로 가는 링크로 바꿉니다. 단 뒤에는 유튜브에서만 지울 수 있습니다.
					</p>
				</>
			)}
			{error && <p className="state state--error">{error}</p>}
			<span className="sm-actions">
				{!data.blocked && (
					<button
						type="button"
						className="button button--small sm-go"
						disabled={sending || body.trim() === ""}
						onClick={send}
					>
						{sending ? "다는 중…" : "답글 달기"}
					</button>
				)}
				<button type="button" className="button button--small" disabled={sending} onClick={onCancel}>
					닫기
				</button>
			</span>
		</div>
	);
}
