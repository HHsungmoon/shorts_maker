import type { ReactNode } from "react";

interface StepProps {
	no: number;
	title: string;
	done: boolean;
	// 앞 단계가 끝나 지금 눌러야 하는 단계. 하나만 강조해 다음 행동을 분명히 한다.
	next: boolean;
	detail: ReactNode;
	actions?: ReactNode;
}

export function Step({ no, title, done, next, detail, actions }: StepProps) {
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
			</div>
		</section>
	);
}
