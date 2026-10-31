"""Day 5: stdlib only. fastmcp未導入のPCでもロジックを検証可能。"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if "fastmcp" not in sys.modules:
    fake = types.ModuleType("fastmcp")

    class FastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            return lambda f: f

    fake.FastMCP = FastMCP
    sys.modules["fastmcp"] = fake

spec = importlib.util.spec_from_file_location("reception_under_test", HERE / "reception_server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def guide(purpose, text, name="", **kwargs):
    return app.get_reception_guidance(purpose, text, name, **kwargs)


class ReceptionTests(unittest.TestCase):
    def test_day4_tanaka(self):
        r = guide("delivery", "田中さんに荷物を届けます", "田中")
        self.assertTrue(r["success"])
        self.assertEqual(r["destination"], "青い7番受付")
        self.assertEqual(r["direction"], "left")
        self.assertEqual(r["staff_id"], "P001")

    def test_day4_sato(self):
        r = guide("delivery", "佐藤さんに荷物を届けます", "佐藤")
        self.assertEqual(r["destination"], "管理部受付")
        self.assertEqual(r["direction"], "right")

    def test_day4_meeting(self):
        r = guide("meeting", "佐藤さんと会議です", "佐藤")
        self.assertEqual(r["direction"], "front")

    def test_unknown_purpose(self):
        r = guide("meeting", "田中さんいます？", "田中")
        self.assertEqual(r["reason"], "purpose_unknown")
        self.assertNotIn("direction", r)

    def test_ambiguous_surname(self):
        r = guide("delivery", "山田さんに荷物です", "山田")
        self.assertEqual(r["reason"], "ambiguous_staff")
        self.assertEqual(len(r["candidates"]), 2)
        self.assertNotIn("direction", r)

    def test_given_name_disambiguates(self):
        r = guide("delivery", "山田恵子さんに荷物を届けたい", "山田")
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P004")
        self.assertEqual(r["direction"], "right")

    def test_group_disambiguates(self):
        r = guide("delivery", "企画調整グループの山田さんに荷物です", "山田")
        self.assertEqual(r["staff_id"], "P003")
        self.assertEqual(r["direction"], "left")

    def test_multiturn_context(self):
        r = guide("delivery", "企画調整グループの方です", "山田", prior_utterance="山田さんに荷物を届けに来ました")
        self.assertEqual(r["staff_id"], "P003")

    def test_staff_id_is_not_sole_evidence(self):
        r = guide("delivery", "山田さんに荷物です", "山田", staff_id="P003")
        self.assertEqual(r["reason"], "ambiguous_staff")

    def test_homophone_kanji(self):
        r = guide("delivery", "斎藤一郎さんに荷物です", "斎藤一郎")
        self.assertEqual(r["reason"], "ambiguous_staff")
        self.assertEqual(len(r["candidates"]), 2)

    def test_homophone_kana(self):
        r = app.search_staff_candidates("さいとういちろう")
        self.assertEqual(r["matched_count"], 2)

    def test_asr_group_typo_is_caught(self):
        # 過去の実機ログで「企画調整」が「規画調整」と認識されたケース。
        r = guide(
            "delivery", "規画調整グループの斎藤さんです", "斎藤", staff_id="P005",
            prior_utterance="斎藤さんに荷物を届けたい",
        )
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P005")
        self.assertEqual(r["direction"], "left")

    def test_asr_kikaku_kikaku_kanji_confusion(self):
        # 「企画調整」を「規格調整」と認識しても、明示の別名として照合できる。
        r = guide("delivery", "規格調整グループの斎藤さんに荷物です", "斎藤", staff_id="P005")
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P005")

    def test_identical_reading_asks_group_not_given_name_in_guidance(self):
        r = guide("delivery", "斎藤さんに荷物です", "斎藤")
        self.assertEqual(r["reason"], "ambiguous_staff")
        self.assertIn("企画調整グループ", r["message"])
        self.assertIn("計測技術グループ", r["message"])
        self.assertNotIn("下のお名前", r["message"])

    def test_identical_reading_asks_group_in_search(self):
        r = app.search_staff_candidates("斎藤")
        self.assertEqual(r["matched_count"], 2)
        self.assertIn("企画調整グループ", r["message"])
        self.assertIn("計測技術グループ", r["message"])
        self.assertNotIn("下のお名前", r["message"])

    def test_saito_variant_surfaces_both_candidates(self):
        # 同音異字は本人確定せず、2人を候補に残す。
        r = app.search_staff_candidates("斉藤")
        self.assertEqual({p["staff_id"] for p in r["candidates"]}, {"P005", "P006"})
        r = guide("delivery", "斉藤さんに荷物です", "斉藤", staff_id="P005")
        self.assertEqual(r["reason"], "ambiguous_staff")

    def test_saito_fullname_variant_keeps_both_candidates(self):
        r = app.search_staff_candidates("斉藤一郎")
        self.assertEqual({p["staff_id"] for p in r["candidates"]}, {"P005", "P006"})

    def test_saito_variant_group_can_disambiguate(self):
        r = guide("delivery", "斉藤さんに荷物です。規格調整グループです", "斉藤", staff_id="P005")
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P005")

    def test_saito_variant_group_disallows_wrong_id(self):
        r = guide("delivery", "斉藤さんに荷物です。規格調整グループです", "斉藤", staff_id="P006")
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "staff_id_mismatch")

    def test_bare_yes_is_not_accepted_as_evidence(self):
        r = guide(
            "delivery", "はい、そうです", "斎藤", staff_id="P005",
            prior_utterance="斎藤さんに荷物を届けたい",
        )
        self.assertFalse(r["success"])
        self.assertFalse(r["needs_clarification"])
        self.assertEqual(r["reason"], "ambiguous_staff_unresolved")
        self.assertNotIn("direction", r)

    def test_bare_yes_with_prior_group_keeps_resolution(self):
        # 先行発話に所属があれば、そこに基づく確定は認める。
        r = guide(
            "delivery", "はい、そうです", "斎藤", staff_id="P005",
            prior_utterance="企画調整グループの斎藤さんに荷物を届けたい",
        )
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P005")

    def test_wrong_staff_id_even_with_alias_is_rejected(self):
        r = guide(
            "delivery", "規画調整グループの斎藤さんです", "斎藤", staff_id="P006",
            prior_utterance="斎藤さんに荷物を届けたい",
        )
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "staff_id_mismatch")

    def test_group_dual(self):
        r = app.search_staff_candidates("加藤")
        self.assertEqual(set(r["candidates"][0]["groups"]), {"企画調整グループ", "計測技術グループ"})

    def test_center_attached(self):
        r = app.search_staff_candidates("西村")
        self.assertEqual(r["candidates"][0]["groups"], ["センター付"])

    def test_external_facility(self):
        r = app.get_facility_guidance("オフサイトセンター")
        self.assertFalse(r["managed_by_us"])
        self.assertEqual(r["direction"], "left")

    def test_external_without_staff_lookup(self):
        r = guide("unknown", "地域防災連絡所の鈴木さんに会いたいです", "鈴木")
        self.assertTrue(r["success"])
        self.assertFalse(r["managed_by_us"])
        self.assertIn("別の組織", r["message"])

    def test_unknown_staff(self):
        r = guide("delivery", "鈴木さんに荷物です", "鈴木")
        self.assertEqual(r["reason"], "staff_not_found")

    def test_wrong_staff_name_is_rejected(self):
        r = guide("delivery", "佐藤さんに荷物です", "田中")
        self.assertEqual(r["reason"], "staff_name_mismatch")

    def test_conflicting_group_hint_is_rejected(self):
        r = guide("delivery", "企画調整グループの山田さんに荷物です", "山田", group_hint="研修支援グループ")
        self.assertEqual(r["reason"], "group_hint_mismatch")

    def test_tool_dropped_name(self):
        r = guide("delivery", "佐藤さんに荷物です", "")
        self.assertEqual(r["staff_id"], "P002")

    def test_facility_intro(self):
        r = app.get_center_info("description")
        self.assertIn("架空", r["message"])

    def test_invalid_route_rejected(self):
        d = app.load_data()
        d["staff"]["P001"]["routes"]["delivery"] = "DOES_NOT_EXIST"
        with self.assertRaisesRegex(ValueError, "unknown route"):
            app.validate_data(d)


    def test_ocr_label_asks_confirmation_first(self):
        r = app.get_label_delivery_guidance("斎藤一郎", "企画調整グループ")
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "label_needs_confirmation")
        self.assertNotIn("direction", r)

    def test_ocr_label_group_disambiguates_left(self):
        r = app.get_label_delivery_guidance("斎藤一郎", "企画調整グループ", True)
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P005")
        self.assertEqual(r["direction"], "left")

    def test_ocr_label_group_disambiguates_right(self):
        r = app.get_label_delivery_guidance("齋藤一郎", "計測技術グループ", True)
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P006")
        self.assertEqual(r["direction"], "right")

    def test_ocr_kanji_variant_with_group(self):
        r = app.get_label_delivery_guidance("斉藤一郎", "規格調整グループ", True)
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P005")

    def test_ocr_no_group_must_not_auto_select(self):
        r = app.get_label_delivery_guidance("斎藤一郎", "", True)
        self.assertFalse(r["success"])
        self.assertNotIn("direction", r)

    def test_ocr_unknown_group_must_not_auto_select(self):
        r = app.get_label_delivery_guidance("斎藤一郎", "視覚調整グループ", True)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "label_group_unrecognized")

    def test_ocr_conflicting_group_must_not_auto_select(self):
        r = app.get_label_delivery_guidance("斎藤一郎", "企画調整グループ 計測技術グループ", True)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "label_group_unrecognized")

    def test_ocr_unknown_staff_not_found(self):
        r = app.get_label_delivery_guidance("鈴木三郎", "企画調整グループ", True)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "label_staff_not_found")

    def test_ocr_staff_group_mismatch_not_found(self):
        r = app.get_label_delivery_guidance("佐藤花子", "企画調整グループ", True)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "label_name_group_mismatch")


    def test_ocr_unknown_staff_asks_confirmation_before_lookup(self):
        r = app.get_label_delivery_guidance("鈴木三郎", "企画調整グループ", False)
        self.assertEqual(r["reason"], "label_needs_confirmation")
        self.assertIn("鈴木三郎", r["message"])
        self.assertNotIn("direction", r)

    def test_ocr_unknown_group_asks_confirmation_before_lookup(self):
        r = app.get_label_delivery_guidance("斎藤一郎", "不明グループ", False)
        self.assertEqual(r["reason"], "label_needs_confirmation")
        self.assertIn("不明グループ", r["message"])
        self.assertNotIn("direction", r)

    def test_ocr_unknown_staff_after_confirmation_offers_correction(self):
        r = app.get_label_delivery_guidance("鈴木三郎", "企画調整グループ", True)
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "label_staff_not_found")
        self.assertTrue(r["needs_clarification"])
        self.assertIn("再撮影", r["message"])
        self.assertIn("口頭", r["message"])
        self.assertNotIn("direction", r)

    def test_ocr_unknown_group_after_confirmation_offers_correction(self):
        r = app.get_label_delivery_guidance("斎藤一郎", "不明グループ", True)
        self.assertFalse(r["success"])
        self.assertTrue(r["needs_clarification"])
        self.assertIn("口頭", r["message"])
        self.assertNotIn("direction", r)

    def test_ocr_name_group_mismatch_after_confirmation_offers_correction(self):
        r = app.get_label_delivery_guidance("佐藤花子", "企画調整グループ", True)
        self.assertFalse(r["success"])
        self.assertTrue(r["needs_clarification"])
        self.assertIn("再撮影", r["message"])
        self.assertNotIn("direction", r)

    def test_voice_correction_requires_confirmation_then_routes(self):
        r = app.get_label_delivery_guidance("齋藤一郎", "計測技術グループ", False, source="voice_correction")
        self.assertEqual(r["reason"], "label_needs_confirmation")
        self.assertNotIn("direction", r)
        self.assertIn("口頭", r["message"])
        yes = app.get_label_delivery_guidance("齋藤一郎", "計測技術グループ", True, source="voice_correction")
        self.assertTrue(yes["success"])
        self.assertEqual(yes["staff_id"], "P006")
        self.assertEqual(yes["direction"], "right")

    def test_ocr_incomplete_offers_alternative(self):
        r = app.get_label_delivery_guidance("斎藤一郎", "", False)
        self.assertFalse(r["success"])
        self.assertIn("口頭", r["message"])
        self.assertNotIn("direction", r)



if __name__ == "__main__":
    unittest.main(verbosity=2)
