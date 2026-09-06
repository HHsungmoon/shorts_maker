"""외부 도구 어댑터 — 프로세스 밖의 것과 말하는 코드만.

ffmpeg.py  ffmpeg/ffprobe subprocess
gemini.py  google-genai (LLM · 임베딩)
ytdlp.py   yt-dlp subprocess (유튜브 다운로드 · 메타데이터)
여기엔 DB 도 파이프라인 개념도 없다. 도구를 바꾸면 이 폴더만 바뀐다.
"""
