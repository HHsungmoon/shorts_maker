import { useState } from "react";
import { Link } from "react-router-dom";
import { fetchYoutubeUploadForm } from "../api";
import { Modal } from "../../shared/Modal";
import { useAsync } from "../../shared/useAsync";
import type { ShortsClip, ShortsClipUpload, YoutubePrivacy, YoutubeUploadDraft, YoutubeUploadForm } from "../types";

// 유튜브가 받는 한도(videos.insert). 🔴 넘으면 **잘라 보내지 않고** 사람이 고치게 둔다 — 개발자 정책
// III.C.3 은 사람이 쓴 값을 바꿔 보내는 것을 막는다. 서버(upload.check_metadata)도 같은 선에서 거절한다.
const TITLE_MAX = 100;
const DESCRIPTION_MAX_BYTES = 5000;

const PRIVACY_LABEL: Record<YoutubePrivacy, string> = {
	private: "비공개",
	unlisted: "일부 공개",
	public: "공개",
};

const PRIVACY_HINT: Record<YoutubePrivacy, string> = {
	private: "나만 봅니다. 채널에서 먼저 확인하고 공개로 바꿀 때",
	unlisted: "링크가 있는 사람만 봅니다",
	public: "누구나 봅니다. 구독자 피드와 Shorts 탭에 뜹니다",
};

/**
 * 유튜브에 올리기 — 무엇이 어디로 어떻게 올라가는지 보여주고 사람이 고른 뒤 보낸다.
 *
 * 🔴 이 대화상자의 [올리기] 한 번이 개발자 정책이 말하는 업로드마다의 **명시적 동의** 다(III.I.2). 그래서
 * 채널 · 공개 범위를 보내기 전에 반드시 보여주고, 공개 범위는 서버에 기본값이 없다(III.E.3).
 */
export function YoutubeUploadModal({
	clip,
	onClose,
	onSubmit,
}: {
	clip: ShortsClip;
	onClose: () => void;
	onSubmit: (draft: YoutubeUploadDraft) => void;
}) {
	const form = useAsync<YoutubeUploadForm>(() => fetchYoutubeUploadForm(clip.id), [clip.id]);
	return (
		<Modal
			open
			title="유튜브에 올리기"
			onClose={onClose}
			subhead="연결된 채널에 Shorts 로 올라갑니다. 올린 뒤에는 유튜브 스튜디오에서 고칩니다."
		>
			{form.loading && <p className="state">불러오는 중…</p>}
			{form.error && <p className="state state--error">{form.error.message}</p>}
			{form.data && <UploadForm form={form.data} onClose={onClose} onSubmit={onSubmit} />}
		</Modal>
	);
}

function UploadForm({
	form,
	onClose,
	onSubmit,
}: {
	form: YoutubeUploadForm;
	onClose: () => void;
	onSubmit: (draft: YoutubeUploadDraft) => void;
}) {
	// 기본값은 서버가 만든다(제목 · 원본 링크 · #Shorts). 읽힌 뒤에야 이 폼이 생기므로 처음 값으로 바로 채운다.
	const [draft, setDraft] = useState<YoutubeUploadDraft>(form.defaults);

	const channel = form.channel;
	const configured = form.configured;
	const title = draft.title.trim();
	const descriptionBytes = new TextEncoder().encode(draft.description.trim()).length;
	const brackets = /[<>]/.test(`${draft.title}${draft.description}`);
	const problem = !configured
		? "서버에 유튜브 설정이 없습니다(.env 의 SHORTS_YOUTUBE_CLIENT_ID · SECRET)"
		: !channel
			? "채널이 연결되지 않았습니다"
			: title.length === 0
				? "제목이 비었습니다"
				: title.length > TITLE_MAX
					? `제목이 ${TITLE_MAX}자를 넘습니다`
					: descriptionBytes > DESCRIPTION_MAX_BYTES
						? "설명이 너무 깁니다"
						: brackets
							? "유튜브는 제목·설명의 꺾쇠(< >)를 받지 않습니다"
							: null;
	const done = form.uploads.filter((u) => u.status === "DONE");

	return (
		<form
			className="sm-yt-form"
			onSubmit={(event) => {
				event.preventDefault();
				if (!problem) {
					onSubmit({ ...draft, title, description: draft.description.trim() });
				}
			}}
		>
			<p className="sm-yt-form__channel">
				{channel ? (
					<>
						올라갈 채널 <strong>{channel.channel_title ?? "연결된 채널"}</strong>
						{!channel.channel_title && (
							<span className="sm-meta"> · 채널 이름은 첫 업로드 뒤에 보입니다</span>
						)}
					</>
				) : (
					<>
						채널이 연결되지 않았습니다 — <Link to="/studio">홈</Link> 오른쪽 위 [유튜브 연결] 에서 먼저
						연결하세요.
					</>
				)}
			</p>

			{done.length > 0 && (
				// 🔴 같은 클립을 또 올리면 채널에 같은 영상이 하나 더 생긴다. 막지는 않는다(비공개로 시험한 뒤
				// 공개로 다시 올리는 경우가 있다) — 알고 누르게만 한다.
				<p className="notice">
					이 클립은 이미 {done.length}번 올렸습니다. 다시 올리면 채널에 영상이 하나 더 생깁니다.
				</p>
			)}

			<label className="field">
				<span className="field__label">
					제목 <span className="sm-meta">{title.length}/{TITLE_MAX}</span>
				</span>
				<input
					className="field__input"
					value={draft.title}
					autoFocus
					onChange={(event) => setDraft({ ...draft, title: event.target.value })}
				/>
			</label>

			<label className="field">
				<span className="field__label">
					설명 <span className="sm-meta">{descriptionBytes}/{DESCRIPTION_MAX_BYTES}바이트</span>
				</span>
				<textarea
					className="field__input sm-yt-form__description"
					rows={5}
					value={draft.description}
					onChange={(event) => setDraft({ ...draft, description: event.target.value })}
				/>
				{/* 🔴 Shorts 설명의 링크는 눌리지 않는다(유튜브 정책). 원본으로 가는 눌리는 길은 관련 동영상
				    하나뿐인데 API 로는 못 단다 — 올린 뒤 스튜디오에서 지정한다. */}
				<span className="sm-meta">
					Shorts 설명의 링크는 눌리지 않습니다. 원본으로 가는 길은 올린 뒤 유튜브 스튜디오의 “관련
					동영상” 에서 지정하세요.
				</span>
			</label>

			<fieldset className="sm-yt-form__privacy">
				<legend className="field__label">공개 범위</legend>
				{(Object.keys(PRIVACY_LABEL) as YoutubePrivacy[]).map((privacy) => (
					<label key={privacy} className="sm-yt-form__option">
						<input
							type="radio"
							name="privacy"
							value={privacy}
							checked={draft.privacy === privacy}
							onChange={() => setDraft({ ...draft, privacy })}
						/>
						<span>
							<strong>{PRIVACY_LABEL[privacy]}</strong>
							<span className="sm-meta"> — {PRIVACY_HINT[privacy]}</span>
						</span>
					</label>
				))}
			</fieldset>

			<div className="sm-shorts__titlefoot">
				{problem && <span className="sm-yt-form__problem">{problem}</span>}
				<button type="button" className="button" onClick={onClose}>
					취소
				</button>
				{/* 설정·채널·한도 문제는 바로 위에 이유가 적혀 있어 잠가도 된다. 보기 전용은 잠그지 않는다 —
				    submit 이 거절 토스트를 띄운다(CLAUDE.md 인증 경계). */}
				<button type="submit" className="button sm-go" disabled={problem !== null}>
					{PRIVACY_LABEL[draft.privacy]}로 올리기
				</button>
			</div>
		</form>
	);
}

/** 카드에 붙는 한 줄 — 가장 최근에 올린 것이 어떻게 됐나. */
export function YoutubeUploadLine({ upload }: { upload: ShortsClipUpload }) {
	if (upload.status === "UPLOADING") {
		return <p className="sm-short__yt">유튜브에 올리는 중…</p>;
	}
	if (upload.status === "FAILED") {
		return (
			<p className="sm-short__yt sm-short__yt--failed" title={upload.error ?? undefined}>
				유튜브 업로드 실패 — {upload.error ?? "이유 없음"}
			</p>
		);
	}
	return (
		<p className="sm-short__yt">
			유튜브 {PRIVACY_LABEL[upload.privacy]}
			{upload.shorts_url && (
				<>
					{" · "}
					<a href={upload.shorts_url} target="_blank" rel="noreferrer">
						보기 ↗
					</a>
				</>
			)}
			{upload.studio_url && (
				<>
					{" · "}
					<a href={upload.studio_url} target="_blank" rel="noreferrer" title="관련 동영상을 원본으로 지정하세요">
						스튜디오에서 원본 연결 ↗
					</a>
				</>
			)}
		</p>
	);
}
