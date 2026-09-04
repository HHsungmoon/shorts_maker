"""테스트용 Config 만들기.

🔴 Config 를 테스트에서 직접 생성하면 필드를 추가할 때마다 전 테스트가 깨진다(실제로 두 번
겪었다). `dataclasses.replace` 로 실제 로딩 결과를 덮어쓰면 새 필드는 자동으로 채워지고,
테스트가 신경 쓰는 값만 명시하게 된다.
"""

import dataclasses
from pathlib import Path

from shorts_maker import config


def make_config(root: Path, **overrides) -> config.Config:
    base = dataclasses.replace(
        config.load(),
        # 실제 .env 의 키가 테스트로 새어들지 않게 한다.
        gemini_api_key="",
        api_token="",
        db_path=root / "test.db",
        work_dir=root / "work",
        source_dir=root / "sources",
    )
    return dataclasses.replace(base, **overrides)
