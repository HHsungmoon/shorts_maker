import { PrefMenu } from "../../shared/PrefMenu";
import {
	GUTTERS,
	THEMES,
	loadGutter,
	loadTheme,
	saveGutter,
	saveTheme,
} from "../../shared/theme";

/**
 * 색상 고르기. 로그아웃 왼쪽에 둔다.
 *
 * 테마가 몇 개가 될지 몰라서(2026-10-24) 버튼을 늘어놓지 않고 눌러야 펼쳐지는 메뉴로 만들었다.
 * 🔴 보기 전용도 고를 수 있다 — 이 브라우저의 화면 색일 뿐 서버의 무엇도 바꾸지 않는다.
 */
export function ThemePicker() {
	return (
		<PrefMenu
			label="색상"
			options={THEMES}
			load={loadTheme}
			save={saveTheme}
			icon={(option) => <span className="sm-theme__dot" style={{ background: option.swatch }} aria-hidden="true" />}
		/>
	);
}

/** 좌우 여백 고르기. 영상 화면에만 둔다 — 홈과 프롬프트 화면은 원래 가운데 1100px 라 해당이 없다. */
export function GutterPicker() {
	return (
		<PrefMenu
			label="여백"
			options={GUTTERS}
			load={loadGutter}
			save={saveGutter}
			icon={(option) => (
				<span className="sm-theme__gutter" aria-hidden="true">
					<span style={{ marginInline: `${Number(option.id) / 2}px` }} />
				</span>
			)}
		/>
	);
}
