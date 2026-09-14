import type { ReactNode } from "react";

interface StepProps {
	no: number;
	title: string;
	done: boolean;
	// 앞 단계가 끝나 지금 눌러야 하는 단계. 하나만 강조해 다음 행동을 분명히 한다.
	next: boolean;
	detail: ReactNode;
	actions?: ReactNode;
	/**
	 * 제목 줄 **아래**에 폭을 다 쓰는 내용. 여러 줄 입력칸처럼 제목 옆 오른쪽 끝(actions)에 들어가기엔
	 * 큰 것을 넣는다 — 그 자리에 넣었더니 긴 기준 문장이 한 줄 입력칸에서 잘려 보였다(2026-09-14).
	 */
	children?: ReactNode;
}

export function Step({ no, title, done, next, detail, actions, children }: StepProps) {
	const className = `sm-step${done ? " sm-step--done" : ""}${next ? " sm-step--next" : ""}`;
	return (
		<section className={className}>
			<span className="sm-step__no">{done ? "✓" : no}</span>
			<div className="sm-step__body">
				<div className="sm-actions">
					<span className="sm-step__title">{title}</span>
					<span className="sm-meta">{detail}</span>
					{actions && <span className="sm-actions sm-actions--end">{actions}</span>}
				</div>
				{children && <div className="sm-step__extra">{children}</div>}
			</div>
		</section>
	);
}
