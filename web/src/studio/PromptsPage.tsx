import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { fetchPrompts, saveReactionGuide, saveStandard } from "./api";
import { StudioHead } from "./components/StudioChrome";
import { useAsync } from "../shared/useAsync";
import type { ReactNode } from "react";
import type { PromptCatalog, PromptPart, PromptStage, PromptStandard } from "./types";
import "./prompts.css";

/**
 * 프롬프트 — 모델에게 무엇이 어떻게 들어가는가.
 *
 * 프롬프트는 네 층으로 조립된다. 이 화면에서 고칠 수 있는 것은 **2층 관리자 기준**과 **시청자 반응 요약 관점**
 * (tease §3-6, 2026-11-01) 둘이다. 🔴 둘은 다른 글이다 — 기준은 클립 쪽에만, 관점은 요약에만 들어간다.
 * 1층 규칙은 보여주기만 한다 — 출력 형식이 한 글자만 어긋나도 파싱이 실패하고, 자립성 관문이
 * 흔들리면 앞뒤를 모르면 이해 못 하는 클립이 조용히 발행된다.
 *
 * 🔴 아래 원문은 **코드의 템플릿 그대로**다. 서버가 요약하지 않고 원문을 보내며, 칸(`{…}`)도
 * 원문에서 뽑는다. 누가 따로 적은 설명이면 코드가 바뀔 때 조용히 어긋난다.
 */
export function PromptsPage() {
	const [token, setToken] = useState(0);
	const catalog = useAsync<PromptCatalog>(fetchPrompts, [token], { keepPrevious: true });

	if (catalog.loading) {
		return <p className="state">불러오는 중…</p>;
	}
	if (catalog.error || !catalog.data) {
		return <p className="state state--error">프롬프트 목록을 읽지 못했습니다.</p>;
	}
	const data = catalog.data;

	return (
		<div className="page">
			<StudioHead title="프롬프트">
				<Link to="/studio" className="button button--small">
					영상 목록으로
				</Link>
			</StudioHead>

			<TextEditor
				title="관리자 기준"
				value={data.standard}
				save={saveStandard}
				onSaved={() => setToken((n) => n + 1)}
				placeholder="예) 숫자·조건·기한이 구체적으로 나오는 발화를 우선한다. 농담 위주의 구간은 낮게 본다."
				clearLabel="기준 지우기"
			>
				<p className="sm-meta">
					이 채널이 공통으로 좋게 보는 것을 적습니다. 기준 경로의 순위·자르기와 질문 경로의 후보 만들기에
					들어가고, 판정에서는 <strong>점수에만</strong> 반영됩니다. 다음 답하기와 클립 만들기부터 적용되며
					이미 만든 것은 바뀌지 않습니다.
				</p>
				<p className="sm-meta">
					🔴 선호를 적는 자리입니다. 모델이 따르지 않을 수 있습니다. &quot;경쟁사 이름은 절대 내보내지
					않는다&quot;처럼 반드시 지켜져야 하는 규칙은 여기가 아니라 코드로 막아야 합니다.
				</p>
			</TextEditor>

			<TextEditor
				title="시청자 반응 요약 관점"
				value={data.reaction}
				save={saveReactionGuide}
				onSaved={() => setToken((n) => n + 1)}
				placeholder="예) 채용 과정·전형 일정에 대한 불만은 따로 모은다. 강연자 말투에 대한 반응은 낮게 본다."
				clearLabel="관점 지우기"
			>
				<p className="sm-meta">
					리포트의 [AI 요약] 이 감상 댓글을 읽을 때 특히 보고 싶은 것을 적습니다. 요약에만 들어가고{" "}
					<strong>클립 쪽(순위·자르기·후보·판정)에는 들어가지 않습니다</strong> — 관리자 기준도 요약에는 들어가지
					않습니다. 다음 [AI 요약] 부터 적용되며 이미 받은 요약은 바뀌지 않습니다.
				</p>
			</TextEditor>

			<h2 className="section-title">네 층</h2>
			<div className="table-wrap">
				<table className="table">
					<thead>
						<tr>
							<th>층</th>
							<th>누가 정하나</th>
							<th>고칠 수 있나</th>
							<th>무엇</th>
						</tr>
					</thead>
					<tbody>
						{data.layers.map((layer) => (
							<tr key={layer.key}>
								<td>
									<span className={`pr-slot pr-slot--${layer.key}`}>{layer.label}</span>
								</td>
								<td className="sm-meta">{layer.who}</td>
								<td className="sm-meta">{layer.editable ? "예" : "아니오"}</td>
								<td className="sm-meta">{layer.note}</td>
							</tr>
						))}
					</tbody>
				</table>
			</div>

			{/* 관리자 숏폼의 컨셉 셋(tease §5-11). 🔴 서버 코드의 글 그대로다 — 화면에서 따로 적지 않는다. */}
			<h2 className="section-title">컨셉 셋</h2>
			<p className="sm-meta">
				새 클립 탭에서 고르는 관리자 숏폼의 컨셉입니다. 고른 컨셉의 글이 고르기 · 자르기 · AI 카피의{" "}
				<span className="pr-slot pr-slot--request">{"{concept_block}"}</span> 칸에 들어갑니다. 코드에 있는 글이라
				여기서는 보이기만 합니다.
			</p>
			{data.concepts.map((concept) => (
				<section key={concept.key} className="pr-stage">
					<div className="sm-actions">
						<span className="pr-stage__name">{concept.label}</span>
						<span className="sm-meta">
							{concept.key} · {concept.summary}
						</span>
					</div>
					<details className="sm-fold">
						<summary>글 원문 보기</summary>
						<pre className="sm-pre">
							{`[고르기 · 자르기 · 카피 공통]\n${concept.goal}\n\n[자르기에 덧붙임]\n${concept.cut}\n\n[카피에 덧붙임]\n${concept.copy}`}
						</pre>
					</details>
				</section>
			))}

			<h2 className="section-title">단계별 원문</h2>
			<p className="sm-meta">
				색이 있는 칸이 실행할 때 채워지는 자리이고, 나머지 글이 고정된 규칙입니다. 관리자 기준은{" "}
				<strong>구간 분할과 답 구간 찾기, 시청자 반응 요약에는 들어가지 않습니다.</strong>
			</p>
			{data.stages.map((stage) => (
				<StageBlock key={stage.key} stage={stage} />
			))}
		</div>
	);
}

/** 프롬프트 화면에서 고치는 글 하나 — 관리자 기준 · 요약 관점이 같은 모양이다. 서버는 둘 다 **덧붙인다**. */
function TextEditor({
	title,
	value,
	save: send,
	onSaved,
	placeholder,
	clearLabel,
	children,
}: {
	title: string;
	value: PromptStandard;
	save: (body: string) => Promise<PromptStandard>;
	onSaved: () => void;
	placeholder: string;
	clearLabel: string;
	children: ReactNode;
}) {
	const saved = value.body;
	const max = value.maxChars;
	// 🔴 편집 중인 글은 따로 들고 있는다. 서버 값으로 매번 덮으면 저장 전에 고친 것이 사라진다.
	const { canAct, refuse } = useAuth();
	const [draft, setDraft] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const text = draft ?? saved;
	// 서버가 앞뒤 공백을 걷어낸 뒤 상한을 본다. 화면도 같은 기준으로 센다.
	const length = text.trim().length;
	const over = length > max;
	const dirty = text.trim() !== saved.trim();

	const save = async () => {
		setBusy(true);
		setError(null);
		try {
			await send(text);
			setDraft(null);
			onSaved();
		} catch (e: unknown) {
			setError(e instanceof Error ? e.message : String(e));
		} finally {
			setBusy(false);
		}
	};

	return (
		<section className="pr-standard">
			<h2 className="section-title">{title}</h2>
			{children}
			<textarea
				className="pr-standard__input"
				value={text}
				rows={6}
				placeholder={placeholder}
				aria-label={title}
				onChange={(event) => setDraft(event.target.value)}
			/>
			<div className="sm-actions">
				<span className={`sm-meta${over ? " pr-counter--over" : ""}`}>
					{length} / {max}자
				</span>
				{value.updatedAt && (
					<span className="sm-meta">마지막 저장 {new Date(value.updatedAt).toLocaleString("ko-KR")}</span>
				)}
				<span className="sm-actions sm-actions--end">
					{dirty && (
						<button type="button" className="button button--small" onClick={() => setDraft(null)} disabled={busy}>
							되돌리기
						</button>
					)}
					{/* 보기 전용도 누를 수 있게 둔다 — 막힌 이유를 토스트가 말한다(DeniedToast).
					    글자 수 초과·변경 없음처럼 **눌러도 의미가 없는** 경우만 비활성화다. */}
					<button
						type="button"
						className="button button--small sm-go"
						onClick={() => (canAct ? save() : refuse())}
						disabled={busy || over || !dirty}
					>
						{busy ? "저장 중…" : length === 0 && saved ? clearLabel : "저장"}
					</button>
				</span>
			</div>
			{error && <p className="state state--error">{error}</p>}
		</section>
	);
}

function StageBlock({ stage }: { stage: PromptStage }) {
	return (
		<section className="pr-stage">
			<div className="sm-actions">
				<span className="pr-stage__name">{stage.name}</span>
				<span className="sm-meta">{stage.path}</span>
				{stage.usesReaction ? (
					<span className="pr-badge pr-badge--reaction">요약 관점 적용 · 관리자 기준 적용 안 함</span>
				) : (
					<span className={`pr-badge${stage.usesStandard ? " pr-badge--on" : ""}`}>
						{stage.usesStandard ? "관리자 기준 적용" : "관리자 기준 적용 안 함"}
					</span>
				)}
			</div>
			<p className="sm-meta">{stage.why}</p>
			<details className="sm-fold">
				<summary>
					원문 보기 <span className="sm-meta">{stage.module}</span>
				</summary>
				{/* 🔴 줄바꿈과 들여쓰기가 뜻을 갖는다. 한 줄로 흘리면 규칙의 구조가 안 보인다. */}
				<pre className="sm-pre">
					{stage.parts.map((part, index) => (
						<Part key={index} part={part} />
					))}
				</pre>
			</details>
		</section>
	);
}

function Part({ part }: { part: PromptPart }) {
	if (part.kind === "text") {
		return <>{part.text}</>;
	}
	const spec = part.spec ? `:${part.spec}` : "";
	return (
		<span className={`pr-slot pr-slot--${part.layer}`} title={LAYER_HINT[part.layer]}>
			{`{${part.name}${spec}}`}
		</span>
	);
}

const LAYER_HINT: Record<string, string> = {
	standard: "2층 관리자 기준 — 이 화면 위에서 고칩니다",
	reaction: "시청자 반응 요약 관점 — 이 화면 위에서 고칩니다",
	context: "3층 영상 개요 — 영상 준비 탭에서 고칩니다",
	request: "4층 이번 요청 — 컨셉 · 기준 입력 또는 시청자 질문",
	auto: "자동 — 파이프라인이 채웁니다",
	rule: "1층 규칙",
};
