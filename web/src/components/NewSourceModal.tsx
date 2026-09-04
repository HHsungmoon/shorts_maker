import { useState } from "react";
import { createSourceFromUrl, registerMedia } from "../api/shorts";
import { Modal } from "./Modal";
import type { ShortsJob, ShortsMediaItem } from "../types";

interface Props {
	open: boolean;
	onClose: () => void;
	unregistered: ShortsMediaItem[];
	languages: Record<string, string>;
	onStarted: (job: ShortsJob) => void;
}

function mb(bytes: number): string {
	return bytes >= 1 << 30
		? `${(bytes / (1 << 30)).toFixed(1)}GB`
		: `${Math.round(bytes / (1 << 20))}MB`;
}

export function NewSourceModal({ open, onClose, unregistered, languages, onStarted }: Props) {
	const [url, setUrl] = useState("");
	// 자동 감지는 앞 30초로 판단해 가끔 틀리고, 틀리면 전사가 통째로 쓸모없어진다.
	const [language, setLanguage] = useState("ko");
	const [error, setError] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	async function start(run: () => Promise<ShortsJob>) {
		setBusy(true);
		setError(null);
		try {
			onStarted(await run());
			setUrl("");
			onClose();
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(false);
		}
	}

	return (
		<Modal open={open} title="새로 만들기" onClose={onClose}>
			{error && <p className="state state--error">{error}</p>}

			<div className="field">
				<label className="field__label" htmlFor="source-url">
					유튜브 URL — 서버가 직접 받습니다
				</label>
				<input
					id="source-url"
					className="field__input"
					placeholder="https://www.youtube.com/watch?v=..."
					value={url}
					onChange={(event) => setUrl(event.target.value)}
				/>
			</div>
			<div className="field" style={{ marginTop: 12 }}>
				<label className="field__label" htmlFor="source-language">
					음성 언어 — 나중에 3단계에서 바꿀 수 있습니다
				</label>
				<select
					id="source-language"
					className="field__input"
					value={language}
					onChange={(event) => setLanguage(event.target.value)}
				>
					{Object.entries(languages).map(([code, label]) => (
						<option key={code} value={code}>
							{label}
						</option>
					))}
					<option value="">자동 감지</option>
				</select>
			</div>

			<div className="sm-actions" style={{ marginTop: 10 }}>
				<button
					type="button"
					className="button button--small sm-go"
					disabled={busy || !url.trim()}
					onClick={() => start(() => createSourceFromUrl(url.trim(), language || null))}
				>
					받아서 등록
				</button>
				<span className="sm-meta">유튜브 주소만 받습니다</span>
			</div>

			{unregistered.length > 0 && (
				<>
					<h3 className="section-title">서버에 있는 영상</h3>
					<p className="sm-meta">등록되지 않은 파일입니다. 등록하면 단계 진행이 가능합니다.</p>
					{unregistered.map((item) => (
						<div className="sm-actions" key={item.name} style={{ marginTop: 8 }}>
							<span>{item.name}</span>
							<span className="sm-meta">{mb(item.sizeBytes)}</span>
							<span className="sm-actions sm-actions--end">
								<button
									type="button"
									className="button button--small"
									disabled={busy}
									onClick={() => start(() => registerMedia(item.name, item.name, language || null))}
								>
									등록
								</button>
							</span>
						</div>
					))}
				</>
			)}
		</Modal>
	);
}
