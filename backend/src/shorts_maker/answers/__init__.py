"""시청자 질문 → 답 클립 (docs/tease.md, update_plan.md §3). 제품명(TEASE)은 코드에 안 쓴다.

M2 부터 채워진다: embeddings(임베딩 저장·코사인) · clusters([집계] · 상태 기계 transition()) ·
routing(검색/rank 분기) · retrieval · judge · events(시청자 퍼널). 기존 pipeline 을 부르되 거꾸로는 안 된다.
"""
