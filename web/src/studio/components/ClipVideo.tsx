interface ClipVideoProps {
	src: string;
	width: number;
}

// admin-web 의 LazyVideo 를 대체한다.
//
// 그 컴포넌트는 blob 을 받아 object URL 을 만들고 언마운트 때 revoke 하는 게 본체였다.
// 클립이 `/admin/**` 이라 Authorization 헤더가 필요한데 <video src> 는 헤더를 못 붙였기
// 때문이다. 지금은 동일 오리진 + 세션 쿠키라 브라우저가 알아서 보낸다.
//
// 그래서 얻은 것: **브라우저가 range 요청으로 스트리밍한다.** 예전에는 파일 전체를
// 받아야 첫 프레임이 나왔다 — 수십 MB 짜리 클립에서 체감이 컸다.
export function ClipVideo({ src, width }: ClipVideoProps) {
	return (
		<video
			controls
			preload="metadata"
			src={src}
			style={{ width, borderRadius: 8, background: "#000" }}
		/>
	);
}
