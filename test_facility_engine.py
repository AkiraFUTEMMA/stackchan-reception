"""施設案内拡張のテスト。標準ライブラリのみで実行可能。"""
import json
import unittest
from pathlib import Path
from copy import deepcopy
from facility_engine import FacilityGuide, date_agrees

ROOT = Path(__file__).resolve().parent


class FacilityTests(unittest.TestCase):
    def setUp(self):
        self.staff = json.loads((ROOT / "reception_data.json").read_text(encoding="utf8"))
        self.ext = json.loads((ROOT / "facility_data.json").read_text(encoding="utf8"))
        self.g = FacilityGuide(self.ext, self.staff)

    def test_valid_schema(self):
        self.assertTrue(self.g.place_guidance("休憩室")["success"])

    def test_front_private_room_first_to_reception(self):
        r = self.g.place_guidance("企画調整室")
        self.assertTrue(r["must_checkin"])
        self.assertEqual(r["first_destination"]["place_id"], "L_ENTRANCE")
        self.assertEqual(r["direction"], "front")
        self.assertIn("直接入れません", r["message"])

    def test_second_floor_private_room_first_to_reception(self):
        r = self.g.place_guidance("計測技術室")
        self.assertEqual(r["place"]["floor"], "2階")
        self.assertEqual(r["first_destination"]["place_id"], "L_ENTRANCE")
        self.assertEqual(r["direction"], "front")

    def test_external_org_boundary(self):
        r = self.g.place_guidance("オフサイトセンター")
        self.assertEqual(r["direction"], "left")
        self.assertFalse(r["managed_by_us"])
        self.assertIn("別組織", r["message"])

    def test_public_lounge(self):
        r = self.g.place_guidance("休憩ラウンジ")
        self.assertTrue(r["success"])
        self.assertEqual(r["direction"], "left")
        self.assertFalse(r["must_checkin"])

    def test_unknown_place_fails_closed(self):
        r = self.g.place_guidance("架空の秘密の研究室")
        self.assertFalse(r["success"])
        self.assertFalse(r["can_point"])

    def test_staff_homophone_not_auto_resolved(self):
        r = self.g.staff_workplace("斎藤一郎")
        self.assertEqual(r["reason"], "staff_ambiguous")
        self.assertEqual({i["staff_id"] for i in r["candidates"]}, {"P005", "P006"})
        self.assertFalse(r["can_point"])

    def test_staff_with_group(self):
        r = self.g.staff_workplace("斎藤一郎", "計測技術グループ")
        self.assertTrue(r["success"])
        self.assertEqual(r["staff_id"], "P006")
        self.assertEqual(r["workplace"]["place_id"], "L_MEASURE")
        self.assertIsNone(r["is_currently_present"])
        self.assertEqual(r["first_destination"]["place_id"], "L_ENTRANCE")

    def test_staff_unknown_group_not_ignored(self):
        r = self.g.staff_workplace("斎藤一郎", "別のグループ")
        self.assertEqual(r["reason"], "group_unknown")

    def test_staff_without_location_does_not_invent(self):
        r = self.g.staff_workplace("佐藤花子")
        self.assertEqual(r["reason"], "workplace_unregistered")

    def test_training_name_and_first_checkin(self):
        r = self.g.training_guidance("環境モニタリング基礎研修")
        self.assertTrue(r["success"])
        self.assertEqual(r["venue"]["place_id"], "L_TRAINING")
        self.assertEqual(r["first_destination"]["place_id"], "L_ENTRANCE")
        self.assertEqual(r["direction"], "front")

    def test_training_selective_extraction(self):
        r = self.g.training_guidance("モニタリング基礎研修", "2026年10月27日", "第2講習室", "総合受付")
        self.assertTrue(r["success"])

    def test_training_conflicting_checkin(self):
        r = self.g.training_guidance("環境モニタリング基礎研修", document_checkin="第2講習室", verified_fields="document_checkin")
        self.assertEqual(r["reason"], "document_conflict")
        self.assertIn("集合・受付場所", r["conflicting_fields"])
        self.assertFalse(r["can_point"])

    def test_training_conflicting_date(self):
        r = self.g.training_guidance("環境モニタリング基礎研修", document_date="2026-11-02", verified_fields="document_date")
        self.assertEqual(r["reason"], "document_conflict")

    def test_ocr_hallucinated_venue_does_not_override_registered_checkin(self):
        # 10/26実機の再現: 「名古屋市熱田区...」はVLM誤読であり確認済みではない。
        r = self.g.training_guidance(
            "環境モニタリング基礎研修", "2026年10月27日",
            "名古屋市熱田区2番2講堂", "2階 1階 総合受付")
        self.assertTrue(r["success"])
        self.assertEqual(r["direction"], "front")
        self.assertEqual(r["first_destination"]["place_id"], "L_ENTRANCE")
        self.assertEqual(set(r["unverified_ocr_fields"]), {"document_venue", "document_checkin"})
        self.assertEqual(r["source_of_guidance"], "facility_database")

    def test_second_ocr_clears_old_venue_by_omitting_it(self):
        # 再撮影で受付だけ読めたなら、過去の誤読した会場を送らずに再照合。
        r = self.g.training_guidance("環境モニタリング基礎研修",
                                     document_checkin="支援棟1階 総合受付")
        self.assertTrue(r["success"])
        self.assertEqual(r["unverified_ocr_fields"], [])

    def test_contradiction_from_explicit_visitor_confirmation_stops(self):
        r = self.g.training_guidance("環境モニタリング基礎研修",
                                     document_venue="名古屋市熱田区2番2講堂",
                                     verified_fields="document_venue")
        self.assertEqual(r["reason"], "document_conflict")
        self.assertFalse(r["can_point"])

    def test_no_training_match_does_not_infer(self):
        r = self.g.training_guidance("管理者沟通研修")
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "training_not_found")

    def test_unverified_ocr_wrong_date_does_not_block(self):
        r = self.g.training_guidance("環境モニタリング基礎研修",
                                     document_date="2016-10-07")
        self.assertTrue(r["success"])
        self.assertEqual(r["unverified_ocr_fields"], ["document_date"])

    def test_unrecognized_confirmed_field_fails_closed(self):
        r = self.g.training_guidance("環境モニタリング基礎研修",
                                     verified_fields="somewhere_else")
        self.assertEqual(r["reason"], "invalid_verified_fields")

    def test_training_unknown_needs_recheck(self):
        r = self.g.training_guidance("空飛ぶ猫研修")
        self.assertEqual(r["reason"], "training_not_found")

    def test_food_rule(self):
        r = self.g.facility_policy("食事")
        self.assertEqual(r["direction"], "left")
        self.assertIn("休憩ラウンジ", r["message"])

    def test_smoking_rule(self):
        r = self.g.facility_policy("たばこ")
        self.assertEqual(r["direction"], "right")
        self.assertIn("指定喫煙所", r["message"])

    def test_policy_unknown(self):
        r = self.g.facility_policy("駐輪場")
        self.assertFalse(r["success"])

    def test_schema_bad_place_reference(self):
        d=deepcopy(self.ext)
        d["trainings"]["T101"]["venue"]="L_404"
        with self.assertRaises(ValueError):
            FacilityGuide(d,self.staff)

    def test_date_equivalence(self):
        self.assertTrue(date_agrees("10月27日", "2026-10-27"))
        self.assertTrue(date_agrees("2026年10月27日", "2026-10-27"))
        self.assertTrue(date_agrees("2026/10/27", "2026-10-27"))
        self.assertFalse(date_agrees("10月28日", "2026-10-27"))


if __name__=="__main__":
    unittest.main(verbosity=2)
