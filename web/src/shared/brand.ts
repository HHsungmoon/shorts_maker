// 이름 둘 — 화면에 보이는 것과 코드·레포의 것.
//
// 제품명은 CLIPQ 다(2026-09-16 확정, update_plan D14). 도메인이 clipq.cloud 이고 PM 기획서도 같은
// 이름이라, 화면만 옛 이름(TEASE)으로 남으면 처음 들어온 사람이 다른 서비스로 읽는다.
// 레포·파이썬 패키지·도커 서비스 이름은 shorts_maker 로 두고 바꾸지 않는다(D9: 해커톤 중 개명은
// 위험만 있고 이득이 없다). 그래서 사람이 보는 제목은 PRODUCT_NAME, "서버 프로세스가 떠 있는지"
// 같은 운영 문장은 REPO_NAME 을 쓴다.
//
// 🔴 index.html 의 <title> 은 모듈을 import 할 수 없어 같은 값을 손으로 적어뒀다. 여기를 바꾸면
// 거기도 바꾼다.
export const PRODUCT_NAME = "CLIPQ";
export const REPO_NAME = "shorts_maker";
