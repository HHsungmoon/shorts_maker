"""프롬프트 목록 · 관리자 기준 · 영상 개요 API.

🔴 여기서 지키는 것 셋:
  1. **화면에 보이는 규칙이 실제 코드의 규칙이다.** 목록은 템플릿 원문을 그대로 쓰고 칸도 원문에서
     뽑는다. 요약하거나 손으로 적으면 코드가 바뀔 때 조용히 어긋난다
  2. **새 칸이 생기면 누가 채우는지 정해야 한다.** 층이 정해지지 않은 칸은 테스트가 막는다
  3. **관리자 기준이 들어가는 곳 표시가 원문과 일치한다.** 화면엔 적용된다고 나오는데 실제로는
     안 들어가는 일이 없어야 한다
"""

import string
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from shorts_maker import standards
from shorts_maker.answers import judge, routing
from shorts_maker.db import store
from shorts_maker.http import deps, prompt_catalog, server
from shorts_maker.pipeline import cutting, ranking, segmentation

from ..support import make_config, reset_db


class CatalogTest(unittest.TestCase):
    def test_every_slot_has_a_layer(self):
        """🔴 누가 채우는지 정하지 않은 칸을 화면에 내보내지 않는다."""
        for stage in prompt_catalog.STAGES:
            for slot in prompt_catalog.slots(stage["template"]):
                with self.subTest(stage=stage["key"], slot=slot):
                    self.assertIn(slot, prompt_catalog.LAYER_OF)

    def test_the_catalog_reads_the_live_templates(self):
        # 사본이 아니라 모듈의 그 상수여야 한다 — 템플릿을 고치면 화면이 바로 따라온다.
        live = {
            "segment": segmentation.PROMPT_TEMPLATE, "rank": ranking.FIXED_PROMPT,
            "cut": cutting.PROMPT_TEMPLATE, "select": routing.PROMPT,
            "answer": ranking.ANSWER_PROMPT, "judge": judge.PROMPT,
        }
        for stage in prompt_catalog.STAGES:
            with self.subTest(stage=stage["key"]):
                self.assertIs(stage["template"], live[stage["key"]])

    def test_the_standard_goes_exactly_where_it_was_designed_to(self):
        """🔴 구간 분할과 답 구간 찾기에는 안 들어간다(standards.py 머리 주석)."""
        found = {s["key"]: s["usesStandard"] for s in prompt_catalog.stages()}
        self.assertEqual(found, {
            "segment": False, "rank": True, "cut": True,
            "select": False, "answer": True, "judge": True,
        })

    def test_the_parts_rebuild_the_template_exactly(self):
        # 쪼개다 글자 하나라도 빠지면 화면의 규칙이 실제와 달라진다.
        for stage in prompt_catalog.STAGES:
            with self.subTest(stage=stage["key"]):
                rebuilt = ""
                for part in prompt_catalog.parts(stage["template"]):
                    if part["kind"] == "text":
                        rebuilt += part["text"].replace("{", "{{").replace("}", "}}")
                    else:
                        conv = f"!{part['conversion']}" if part["conversion"] else ""
                        spec = f":{part['spec']}" if part["spec"] else ""
                        rebuilt += "{" + part["name"] + conv + spec + "}"
                self.assertEqual(rebuilt, stage["template"])

    def test_every_layer_the_slots_use_is_explained(self):
        explained = {layer["key"] for layer in prompt_catalog.LAYERS}
        self.assertTrue(set(prompt_catalog.LAYER_OF.values()) <= explained)

    def test_only_the_standard_and_context_layers_are_editable(self):
        # 🔴 1층은 보여주기만 한다.
        editable = {layer["key"] for layer in prompt_catalog.LAYERS if layer["editable"]}
        self.assertEqual(editable, {"standard", "context"})


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.url = reset_db()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = deps.cfg
        deps.cfg = make_config(root, web_dir=root / "none", admin_password="", api_token="")
        self.client = TestClient(server.app)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: setattr(deps, "cfg", self._saved))


class PromptsApiTest(ApiTestCase):
    def test_the_list_carries_every_stage_and_the_standard(self):
        body = self.client.get("/api/prompts").json()
        self.assertEqual([s["key"] for s in body["stages"]],
                         ["segment", "rank", "cut", "select", "answer", "judge"])
        self.assertEqual(body["standard"]["body"], "")
        self.assertEqual(body["standard"]["maxChars"], standards.MAX_CHARS)

    def test_saving_a_standard_shows_up_in_the_list(self):
        saved = self.client.put("/api/prompts/standard", json={"body": "숫자가 있는 발화를 우선한다"})
        self.assertEqual(saved.status_code, 200, saved.text)
        body = self.client.get("/api/prompts").json()
        self.assertEqual(body["standard"]["body"], "숫자가 있는 발화를 우선한다")
        self.assertIsNotNone(body["standard"]["updatedAt"])

    def test_a_standard_over_the_cap_is_refused_and_nothing_is_saved(self):
        response = self.client.put(
            "/api/prompts/standard", json={"body": "가" * (standards.MAX_CHARS + 1)}
        )
        self.assertEqual(response.status_code, 422)
        with store.connect(self.url) as conn:
            self.assertIsNone(standards.latest(conn))

    def test_pasted_whitespace_does_not_push_a_valid_standard_over_the_cap(self):
        # 🔴 붙여 넣으며 딸려 온 공백 때문에 멀쩡한 글이 거절되면 안 된다.
        body = "\n\n" + "가" * standards.MAX_CHARS + "   \n"
        self.assertEqual(self.client.put("/api/prompts/standard", json={"body": body}).status_code, 200)

    def test_a_blank_standard_clears_it(self):
        self.client.put("/api/prompts/standard", json={"body": "기준"})
        self.client.put("/api/prompts/standard", json={"body": ""})
        with store.connect(self.url) as conn:
            self.assertIsNone(standards.load(conn))


class SourceContextApiTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        with store.connect(self.url) as conn:
            self.source_id = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status)"
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
            ).fetchone()["id"]
            conn.commit()

    def context(self) -> str | None:
        with store.connect(self.url) as conn:
            return conn.execute(
                "select context from sources where id = %s", (self.source_id,)
            ).fetchone()["context"]

    def test_the_overview_is_saved_trimmed(self):
        response = self.client.patch(
            f"/api/sources/{self.source_id}", json={"context": "  쏘카 개발 직군 채용설명회  "}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.context(), "쏘카 개발 직군 채용설명회")

    def test_a_blank_overview_is_stored_as_none(self):
        # 🔴 빈 문자열로 두면 프롬프트에 "강연 개요: " 만 남는다. 빈 지시문은 모델이 해석한다.
        self.client.patch(f"/api/sources/{self.source_id}", json={"context": "개요"})
        self.client.patch(f"/api/sources/{self.source_id}", json={"context": "   "})
        self.assertIsNone(self.context())

    def test_an_overview_over_the_cap_is_refused(self):
        response = self.client.patch(
            f"/api/sources/{self.source_id}", json={"context": "가" * 501}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.context())

    def test_an_unknown_source_is_404(self):
        self.assertEqual(
            self.client.patch("/api/sources/999999", json={"context": "개요"}).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
