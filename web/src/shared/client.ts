// fetch 래퍼.
//
// admin-web 에서 가져오면서 세 가지가 사라졌다:
//   1. `ApiResponse<T>` 봉투 — Spring 이 씌우던 것이다. shorts_maker 는 값을 그대로 준다
//   2. Authorization 헤더와 refresh 토큰 회전 — 이제 httpOnly 세션 쿠키다
//   3. 오리진 분리 — 프론트를 FastAPI 가 같은 오리진에서 서빙하므로 base URL 이 없다
//
// 🔴 쿠키가 httpOnly 라 JS 는 로그인 여부를 볼 수 없다. 그래서 "만료됐는지"를 미리
// 판단하지 않고, 401 이 오면 그때 로그인 화면으로 되돌린다(onUnauthorized).

export class ApiError extends Error {
	readonly status: number;

	constructor(status: number, message: string) {
		super(message);
		this.name = "ApiError";
		this.status = status;
	}
}

let unauthorizedHandler: (() => void) | null = null;

/** 세션이 끊겼을 때 부를 곳을 등록한다. AuthProvider 가 마운트되면서 채운다. */
export function onUnauthorized(handler: (() => void) | null): void {
	unauthorizedHandler = handler;
}

// FastAPI 의 에러 본문은 {"detail": ...} 인데, 422(검증 실패)에서는 detail 이 객체 배열이다.
// 그대로 화면에 던지면 "[object Object]" 가 뜬다.
function messageFrom(payload: unknown, fallback: string): string {
	if (typeof payload !== "object" || payload === null || !("detail" in payload)) {
		return fallback;
	}
	const detail = (payload as { detail: unknown }).detail;
	if (typeof detail === "string") {
		return detail;
	}
	if (Array.isArray(detail)) {
		const parts = detail
			.map((item) =>
				typeof item === "object" && item !== null && "msg" in item
					? String((item as { msg: unknown }).msg)
					: null,
			)
			.filter((part): part is string => part !== null);
		if (parts.length > 0) {
			return parts.join(", ");
		}
	}
	return fallback;
}

interface RequestOptions {
	method?: string;
	body?: unknown;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
	const { method = "GET", body } = options;

	const response = await fetch(path, {
		method,
		headers: body === undefined ? undefined : { "Content-Type": "application/json" },
		body: body === undefined ? undefined : JSON.stringify(body),
	});

	if (response.status === 401) {
		unauthorizedHandler?.();
		throw new ApiError(401, "로그인이 필요합니다");
	}

	// 204 와 빈 본문에 대비한다 — .json() 이 던지면 에러 메시지가 엉뚱해진다.
	const payload: unknown = await response.json().catch(() => null);

	if (!response.ok) {
		throw new ApiError(response.status, messageFrom(payload, response.statusText));
	}
	return payload as T;
}
