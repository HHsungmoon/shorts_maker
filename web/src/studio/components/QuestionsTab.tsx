import { Link } from "react-router-dom";
import { ClusterPanel } from "./ClusterPanel";
import type { SourceView } from "../SourcePage";

/**
 * 질문이 첫 탭인 건 제품 정의 그대로다(tease §3) — 시청자가 묻고, 크리에이터가 묶고,
 * 답하고, 발행한다. 준비 단계는 그 앞에 한 번 해두는 일이라 매일 보는 자리를 차지하면
 * 안 된다.
 */
export function QuestionsTab({ view }: { view: SourceView }) {
	const { data, clusters, prepDone, jobBusy, submit, reload } = view;
	return (
		<>
			{/* 준비가 안 끝났으면 [답하기] 가 만들 구간 자체가 없다. 질문은 그대로 쌓이므로
			    막지는 않고, 왜 답이 안 나오는지만 여기서 미리 말해 준다. */}
			{data && !prepDone && (
				<p className="sm-meta sm-hint">
					영상 준비가 끝나야 질문에 답할 수 있습니다 —{" "}
					<Link to={`/sources/${data.source.id}/prepare`}>영상 준비</Link> 탭에서 마저 진행하세요.
				</p>
			)}

			{data && (
				<ClusterPanel
					sourceId={data.source.id}
					published={data.source.published}
					busy={jobBusy}
					list={clusters}
					onJob={submit}
					onChanged={reload}
				/>
			)}
		</>
	);
}
