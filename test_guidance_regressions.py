"""Endpoint regressions from the 2026-10-28 fictional-data device trial."""
import unittest

from test_reception import app
from test_facility_registration import DummyMCP
from facility_tools import register_facility_tools


class DirectionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.registry = DummyMCP()
        self.handlers = register_facility_tools(self.registry, app.load_data)

    def assert_motion(self, result, yaw):
        self.assertTrue(result["success"])
        expected = {"coordinate_frame": "visitor", "point": None, "return_to_front": None}
        if yaw is not None:
            expected["point"] = {"yaw": yaw, "pitch": 0, "speed": 500}
            expected["return_to_front"] = {"yaw": 0, "pitch": 0, "speed": 500}
        self.assertEqual(result.get("head_motion"), expected)

    def test_delivery_left_is_visitor_plus_45(self):
        self.assert_motion(app.get_reception_guidance("delivery", "田中さんに荷物です", "田中"), 45)

    def test_delivery_right_is_visitor_minus_45(self):
        self.assert_motion(app.get_reception_guidance("delivery", "佐藤さんに荷物です", "佐藤"), -45)

    def test_meeting_front_requires_no_head_call(self):
        self.assert_motion(app.get_reception_guidance("meeting", "田中さんと会議です", "田中"), None)

    def test_d_prime_lunch_left_is_plus_45(self):
        self.assert_motion(self.registry.tools["get_facility_policy"]("食事"), 45)

    def test_smoking_right_is_minus_45(self):
        self.assert_motion(self.registry.tools["get_facility_policy"]("喫煙"), -45)

    def test_training_points_to_front_checkin_not_right_venue(self):
        self.assert_motion(self.registry.tools["get_training_guidance"]("環境モニタリング基礎研修"), None)

    def test_external_building_uses_visitor_left(self):
        self.assert_motion(app.get_facility_guidance("オフサイトセンター"), 45)

    def test_place_and_workplace_use_first_destination(self):
        self.assert_motion(self.registry.tools["get_place_guidance"]("休憩ラウンジ"), 45)
        self.assert_motion(self.handlers["get_staff_workplace"]("斎藤一郎", "計測技術グループ"), None)

    def test_failed_or_ambiguous_guidance_has_no_motion(self):
        results = [app.get_reception_guidance("delivery", "斎藤さんに荷物です", "斎藤"),
                   self.registry.tools["get_place_guidance"]("未登録の部屋")]
        for result in results:
            with self.subTest(result=result):
                self.assertFalse(result["success"])
                self.assertNotIn("head_motion", result)


class DocumentConfirmationRegressionTests(unittest.TestCase):
    def call(self, **overrides):
        entry = getattr(app, "get_document_reception_guidance", None)
        self.assertTrue(callable(entry), "Document identity needs a meeting-capable MCP entry point")
        args = dict(request_type="meeting", user_utterance="会議なんですけど",
                    document_staff_name="斉藤 一郎", document_group="企画調整グループ")
        args.update(overrides)
        return entry(**args)

    def test_unconfirmed_pair_has_one_mcp_confirmation(self):
        result = self.call()
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "label_needs_confirmation")
        self.assertEqual(result["confirmation_status"], "pending")
        self.assertIn("企画調整グループ", result["message"])
        self.assertIn("斉藤", result["message"])
        self.assertNotIn("direction", result)

    def test_confirmed_pair_routes_without_asking_group_again(self):
        self.call()
        result = self.call(visitor_confirmed=True, confirmation_utterance="はい、そうです")
        self.assertTrue(result["success"])
        self.assertEqual(result["staff_id"], "P005")
        self.assertEqual(result["request_type"], "meeting")
        self.assertEqual(result["direction"], "front")
        self.assertEqual(result["confirmation_status"], "confirmed")
        self.assertNotIn("？", result["message"])

    def test_confirmed_document_delivery_keeps_existing_left_route(self):
        result = self.call(request_type="delivery", user_utterance="荷物を届けたい",
                           visitor_confirmed=True, confirmation_utterance="合っています")
        self.assertTrue(result["success"])
        self.assertEqual(result["direction"], "left")
        self.assertEqual(result["head_motion"]["point"]["yaw"], 45)

    def test_explicit_menkai_is_a_meeting_purpose(self):
        result = self.call(user_utterance="面会なんですけど", visitor_confirmed=True,
                           confirmation_utterance="はい")
        self.assertTrue(result["success"])
        self.assertEqual(result["request_type"], "meeting")

    def test_boolean_without_confirmation_speech_cannot_confirm(self):
        result = self.call(visitor_confirmed=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "document_confirmation_not_explicit")
        self.assertNotIn("head_motion", result)

    def test_negative_or_unclear_confirmation_cannot_route(self):
        for utterance in ("違います", "はい、違います", "わかりません", "撮って", "企画ではありません"):
            with self.subTest(utterance=utterance):
                result = self.call(visitor_confirmed=True, confirmation_utterance=utterance)
                self.assertFalse(result["success"])
                self.assertNotIn("head_motion", result)

    def test_read_only_does_not_turn_into_meeting(self):
        result = self.call(user_utterance="この書類読んで", visitor_confirmed=True,
                           confirmation_utterance="はい")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "purpose_unknown")

    def test_conflicting_purposes_do_not_route(self):
        result = self.call(user_utterance="荷物の配達と会議です", visitor_confirmed=True,
                           confirmation_utterance="はい")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "purpose_unknown")

    def test_document_and_spoken_group_conflict_does_not_route(self):
        result = self.call(user_utterance="計測技術グループの方と会議です",
                           visitor_confirmed=True, confirmation_utterance="はい")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "document_group_mismatch")

    def test_document_and_spoken_name_conflict_does_not_route(self):
        result = self.call(user_utterance="佐藤花子さんと会議です",
                           visitor_confirmed=True, confirmation_utterance="はい")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "document_staff_mismatch")

    def test_external_organization_does_not_reuse_own_staff_directory(self):
        result = self.call(user_utterance="地域防災連絡所の方と会議です")
        self.assertTrue(result["success"])
        self.assertEqual(result["request_type"], "external_organization")
        self.assertFalse(result["managed_by_us"])
        self.assertEqual(result["staff_id"], "")

    def test_missing_group_requests_only_missing_field(self):
        result = self.call(document_group="")
        self.assertFalse(result["success"])
        self.assertEqual(result["missing_fields"], ["document_group"])
        self.assertNotIn("氏名と所属", result["message"])

    def test_unknown_document_group_cannot_be_confirmed_into_database(self):
        result = self.call(document_group="不明グループ", visitor_confirmed=True,
                           confirmation_utterance="はい")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "label_group_unrecognized")

    def test_voice_correction_requires_confirmation_of_new_pair(self):
        result = self.call(document_group="計測技術グループ", source="voice_correction")
        self.assertEqual(result["confirmation_status"], "pending")
        confirmed = self.call(document_group="計測技術グループ", source="voice_correction",
                              visitor_confirmed=True, confirmation_utterance="そうです")
        self.assertTrue(confirmed["success"])
        self.assertEqual(confirmed["staff_id"], "P006")


if __name__ == "__main__":
    unittest.main()
