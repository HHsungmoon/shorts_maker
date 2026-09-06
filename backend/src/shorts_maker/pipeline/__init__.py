"""파이프라인 — 긴 영상에서 클립을 뽑는 단계들 (make_shorts.md §4).

ingest → stt → segmentation → ranking → cutting → render 순서고, orchestrate.run_all 이 한 잡으로 묶는다.
media 는 원본 파일 목록·삭제, subtitles 는 ASS 자막 생성(render 가 쓴다).
각 모듈은 DB 연결과 Config 를 받아 자기 단계만 한다 — HTTP 도 CLI 도 모른다.
"""
