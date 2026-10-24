import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./shared/styles.css";
import { applyGutter, applyTheme, loadGutter, loadTheme } from "./shared/theme";

// 🔴 첫 렌더 **전에** 건다. 컴포넌트 안에서 걸면 기본 색으로 한 번 그려졌다가 바뀌어 화면이 번쩍인다.
applyTheme(loadTheme());
applyGutter(loadGutter());

createRoot(document.getElementById("root")!).render(
	<StrictMode>
		<App />
	</StrictMode>,
);
