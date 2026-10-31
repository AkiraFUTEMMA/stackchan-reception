"""Fictional-data regressions for a single reception case across voice/photo turns."""
import unittest
import copy
from unittest.mock import patch
from test_reception import app


class PurposeCorrectionTests(unittest.TestCase):
    def test_meeting_after_negated_delivery(self):
        result = app.get_document_reception_guidance(
            "meeting", "荷物じゃなくて会議なので", "斉藤一郎", "企画調整グループ")
        self.assertEqual(result.get("reason"), "label_needs_confirmation")

    def test_negative_meeting_never_validates_meeting(self):
        self.assertEqual(app.validate_request_type("meeting", "会議ではありません"), "unknown")

    def test_positive_delivery_and_negative_meeting(self):
        self.assertEqual(app.validate_request_type("delivery", "荷物の配達です。会議ではありません"), "delivery")

    def test_not_only_delivery_stays_ambiguous(self):
        self.assertEqual(app.validate_request_type("meeting", "配達だけではなく会議もあります"), "unknown")


class FlowTests(unittest.TestCase):
    def call(self, utterance="", **kwargs):
        entry = getattr(app, "advance_reception", None)
        self.assertTrue(callable(entry), "A case-aware MCP entry point is required")
        return entry(user_utterance=utterance, **kwargs)

    def photo(self, **kwargs):
        return self.call(source="camera", document_type="parcel_label",
                         staff_name="斉藤一郎", group_name="企画調整グループ", **kwargs)

    def test_photo_recipient_does_not_establish_delivery(self):
        result = self.photo()
        self.assertEqual(result["action"], "ask_purpose")
        self.assertNotIn("head_motion", result)
        self.assertEqual(result["case_summary"]["staff_name"], "斉藤一郎")

    def test_purpose_after_photo_keeps_fields_and_confirms_once(self):
        photo = self.photo()
        pending = self.call("会議です", case_id=photo["case_id"])
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertEqual(pending["case_summary"]["group_name"], "企画調整グループ")
        result = self.call("合ってます", case_id=photo["case_id"],
                           confirmation_token=pending["confirmation_token"])
        self.assertEqual(result["action"], "guide")
        self.assertEqual(result["staff_id"], "P005")
        self.assertEqual(result["direction"], "front")
        self.assertFalse(result["reception_completed"])

    def test_voice_to_image_preserves_delivery_purpose(self):
        start = self.call("斎藤さんに荷物を届けたい")
        self.assertEqual(start["action"], "ask_group")
        unknown = self.call("所属は分かりません", case_id=start["case_id"])
        self.assertEqual(unknown["action"], "offer_document")
        pending = self.photo(case_id=start["case_id"])
        result = self.call("はい", case_id=start["case_id"],
                           confirmation_token=pending["confirmation_token"])
        self.assertEqual(result["action"], "guide")
        self.assertEqual(result["head_motion"]["point"]["yaw"], 45)

    def test_image_to_voice_asks_only_missing_group(self):
        start = self.call("荷物を届けたい")
        photo = self.call(case_id=start["case_id"], source="camera", document_type="parcel_label",
                          staff_name="斉藤一郎")
        self.assertEqual(photo["action"], "ask_group")
        pending = self.call("企画調整グループです", case_id=start["case_id"], source="voice_correction")
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertEqual(pending["case_summary"]["staff_name"], "斉藤一郎")

    def test_purpose_correction_retains_recipient_not_old_confirmation(self):
        start = self.call("荷物を届けたい")
        old = self.photo(case_id=start["case_id"])
        changed = self.call("荷物じゃなくて会議なので", case_id=start["case_id"],
                            confirmation_token=old["confirmation_token"])
        self.assertEqual(changed["action"], "confirm_recipient")
        self.assertNotEqual(changed["confirmation_token"], old["confirmation_token"])
        self.assertEqual(changed["case_summary"]["purpose"], "meeting")
        self.assertEqual(changed["case_summary"]["group_name"], "企画調整グループ")
        result = self.call("はい", case_id=start["case_id"],
                           confirmation_token=changed["confirmation_token"])
        self.assertEqual(result["direction"], "front")

    def test_changed_group_rejects_old_yes_token(self):
        start = self.call("荷物です")
        old = self.photo(case_id=start["case_id"])
        changed = self.call("計測技術グループです", case_id=start["case_id"],
                            group_name="計測技術グループ", source="voice_correction")
        stale = self.call("はい", case_id=start["case_id"], confirmation_token=old["confirmation_token"])
        self.assertEqual(stale["action"], "confirm_recipient")
        self.assertNotIn("head_motion", stale)
        result = self.call("はい", case_id=start["case_id"],
                           confirmation_token=changed["confirmation_token"])
        self.assertEqual(result["staff_id"], "P006")
        self.assertEqual(result["head_motion"]["point"]["yaw"], -45)

    def test_negative_confirmation_cannot_route(self):
        start = self.call("荷物です")
        pending = self.photo(case_id=start["case_id"])
        result = self.call("はい、違います", case_id=start["case_id"],
                           confirmation_token=pending["confirmation_token"])
        self.assertNotEqual(result["action"], "guide")
        self.assertNotIn("head_motion", result)

    def test_second_visitor_does_not_inherit_identity(self):
        first = self.photo()
        second = self.call("会議です")
        self.assertNotEqual(second["case_id"], first["case_id"])
        self.assertEqual(second["action"], "ask_name")
        self.assertEqual(second["case_summary"]["staff_name"], "")

    def test_unknown_case_never_silently_creates_or_routes(self):
        result = self.call("はい", case_id="nonexistent-case")
        self.assertEqual(result["reason"], "case_not_found")
        self.assertNotIn("head_motion", result)

    def test_missing_group_and_no_document_hands_off_with_summary(self):
        start = self.call("斎藤さんに荷物です")
        self.call("所属は分かりません", case_id=start["case_id"])
        result = self.call("書類はありません", case_id=start["case_id"])
        self.assertEqual(result["action"], "handoff")
        self.assertEqual(result["handoff_summary"]["purpose"], "delivery")
        self.assertEqual(result["handoff_summary"]["staff_name"], "斎藤")
        self.assertIn("group_name", result["handoff_summary"]["missing_fields"])

    def test_read_only_preserved_after_photo(self):
        start = self.call("この書類読んで")
        result = self.photo(case_id=start["case_id"])
        self.assertEqual(result["action"], "read_only")
        self.assertNotIn("head_motion", result)

    def test_unique_voice_recipient_does_not_require_photo(self):
        result = self.call("田中さんに荷物です")
        self.assertEqual(result["action"], "guide")
        self.assertEqual(result["staff_id"], "P001")

    def test_success_is_not_repeated_on_thanks(self):
        start = self.call("田中さんに荷物です")
        result = self.call("ありがとう", case_id=start["case_id"])
        self.assertEqual(result["action"], "done")
        self.assertNotIn("head_motion", result)

    def test_unread_group_does_not_repeat_question_already_answered_unknown(self):
        start = self.call("斎藤さんに荷物です")
        self.call("所属はわかりません", case_id=start["case_id"])
        result = self.call(case_id=start["case_id"], source="camera",
                           document_type="parcel_label", staff_name="斉藤一郎")
        self.assertEqual(result["action"], "handoff")
        self.assertIn("group_name", result["handoff_summary"]["missing_fields"])

    def test_negated_staff_is_replaced_by_spoken_correction(self):
        start = self.call("田中さんに荷物です")
        result = self.call("田中さんではなく佐藤さんへの配達です", case_id=start["case_id"],
                           source="voice_correction")
        self.assertEqual(result["action"], "guide")
        self.assertEqual(result["staff_id"], "P002")

    def test_negated_group_is_replaced_by_spoken_correction(self):
        start = self.call("荷物です")
        self.photo(case_id=start["case_id"])
        result = self.call("企画調整グループではなく計測技術グループです",
                           case_id=start["case_id"], source="voice_correction")
        self.assertEqual(result["action"], "confirm_recipient")
        self.assertEqual(result["case_summary"]["group_name"], "計測技術グループ")

    def test_camera_conflict_is_not_cleared_by_bare_yes(self):
        start = self.call("田中さんに荷物です")
        conflict = self.call(case_id=start["case_id"], source="camera",
                             document_type="parcel_label", staff_name="佐藤花子", group_name="管理グループ")
        self.assertEqual(conflict["action"], "ask_correction")
        result = self.call("はい", case_id=start["case_id"])
        self.assertEqual(result["action"], "ask_correction")
        self.assertNotIn("head_motion", result)

    def test_name_correction_clears_other_person_database_group(self):
        start = self.call("田中さんに荷物です")
        result = self.call("斎藤さんの間違いです", case_id=start["case_id"])
        self.assertEqual(result["action"], "ask_group")
        self.assertEqual(result["case_summary"]["group_name"], "")
        self.assertNotIn("head_motion", result)

    def test_unresolved_group_ambiguity_invalidates_old_confirmation(self):
        start = self.call("荷物です")
        old = self.photo(case_id=start["case_id"])
        self.call("企画調整グループか計測技術グループです", case_id=start["case_id"])
        result = self.call("はい", case_id=start["case_id"], confirmation_token=old["confirmation_token"])
        self.assertEqual(result["action"], "ask_group")
        self.assertNotIn("head_motion", result)

    def test_cancelled_and_reinstated_purpose_needs_new_confirmation(self):
        start = self.call("荷物です")
        old = self.photo(case_id=start["case_id"])
        cancelled = self.call("荷物ではありません", case_id=start["case_id"])
        self.assertEqual(cancelled["action"], "ask_purpose")
        current = self.call("荷物です", case_id=start["case_id"])
        self.assertNotEqual(current["confirmation_token"], old["confirmation_token"])
        result = self.call("はい", case_id=start["case_id"], confirmation_token=old["confirmation_token"])
        self.assertEqual(result["action"], "confirm_recipient")

    def test_external_organization_does_not_resolve_internal_staff(self):
        result = self.call("地域防災連絡所の田中さんに荷物です")
        self.assertEqual(result["action"], "guide")
        self.assertEqual(result["request_type"], "external_organization")
        self.assertEqual(result["staff_id"], "")
        self.assertFalse(result["managed_by_us"])

    def test_reading_document_with_purpose_word_never_routes(self):
        for text in ("田中さんの配達伝票を読んで", "田中さんとの会議の資料を読み取って"):
            with self.subTest(text=text):
                result = self.call(text)
                self.assertEqual(result["action"], "read_only")
                self.assertNotIn("head_motion", result)

    def test_delivery_purpose_plus_reading_label_stays_delivery(self):
        result = self.call("斎藤さんに荷物を届けたいので、この宛名を読んで")
        self.assertEqual(result["case_summary"]["purpose"], "delivery")

    def test_multiple_spoken_recipients_need_one_recipient(self):
        result = self.call("田中太郎さんと佐藤花子さんに荷物です")
        self.assertEqual(result["action"], "ask_name")
        self.assertNotIn("head_motion", result)

    def test_ambiguity_then_new_name_does_not_restore_old_database_group(self):
        start = self.call("田中さんに荷物です")
        self.call("田中さんか斎藤さんです", case_id=start["case_id"])
        result = self.call("斎藤さんです", case_id=start["case_id"])
        self.assertEqual(result["action"], "ask_group")
        self.assertEqual(result["case_summary"]["group_name"], "")

    def test_photo_after_external_guidance_does_not_query_own_staff(self):
        start = self.call("地域防災連絡所の田中さんに荷物です")
        result = self.photo(case_id=start["case_id"])
        self.assertEqual(result["action"], "done")
        self.assertEqual(result["case_summary"]["staff_name"], "")

    def test_unknown_name_offers_document_instead_of_repeating_question(self):
        start = self.call("荷物です")
        result = self.call("名前はわかりません", case_id=start["case_id"])
        self.assertEqual(result["action"], "offer_document")

    def test_unknown_name_and_unreadable_photo_hands_off(self):
        start = self.call("荷物です")
        self.call("名前はわかりません", case_id=start["case_id"])
        result = self.call(case_id=start["case_id"], source="camera", document_type="unreadable")
        self.assertEqual(result["action"], "handoff")
        self.assertIn("staff_name", result["handoff_summary"]["missing_fields"])

    def test_explicit_delivery_and_document_request_prepares_camera(self):
        result = self.call("配達です。この宛名を読んでください")
        self.assertEqual(result["action"], "prepare_camera")
        self.assertEqual(result["case_summary"]["purpose"], "delivery")

    def test_available_document_does_not_repeat_group_question(self):
        start = self.call("斎藤さんに荷物です")
        self.call("所属はわかりません", case_id=start["case_id"])
        result = self.call("はい、書類あります", case_id=start["case_id"])
        self.assertEqual(result["action"], "prepare_camera")


class PracticalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.data = copy.deepcopy(app.load_data())
        self.data["reception"]["delivery_contact"] = {
            "destination": "デモ用内線電話", "direction": "right",
            "message": "右側のデモ用内線電話をご利用ください。", "contact_method": "extension"}
        for sid, given, reading, gid, extension in (
            ("P011", "将大", "まさひろ", "G01", "8102"),
            ("P012", "健一", "けんいち", "G02", "8202"),
        ):
            self.data["staff"][sid] = {
                "family_name": "森川", "given_name": given,
                "reading": "もりかわ" + reading, "family_reading": "もりかわ",
                "group_ids": [gid], "demo_extension": extension,
                "routes": {"delivery": "delivery_contact", "meeting": "meeting"}}
        self.loader = patch.object(app, "load_data", return_value=self.data)
        self.loader.start()
        self.addCleanup(self.loader.stop)
        from reception_flow import ReceptionFlow
        self.flow = ReceptionFlow(app.load_data, app._document_recipient_guidance,
                                  app.staff_candidates, app.matched_group_ids,
                                  app.evidence_staff_query,
                                  delivery_destination=app._delivery_meetup_guidance)

    def photo(self, case_id, **kwargs):
        return self.flow.advance("", case_id=case_id, source="camera",
                                 document_type="parcel_label", **kwargs)

    def test_shared_surname_with_different_given_names_asks_name(self):
        result = self.flow.advance("森川さんに荷物を届けたいんですけど")
        self.assertEqual(result["action"], "ask_name")
        self.assertNotIn("head_motion", result)

    def test_correct_registered_reading_needs_no_photo(self):
        for spoken in ("もりかわまさひろさんに荷物です", "森川まさひろさんに荷物です"):
            with self.subTest(spoken=spoken):
                result = self.flow.advance(spoken)
                self.assertEqual(result["action"], "guide")
                self.assertEqual(result["contact"]["extension"], "8102")

    def test_multiple_spoken_readings_never_pick_one(self):
        result = self.flow.advance("もりかわまさひろさんか、もりかわけんいちさんに荷物です")
        self.assertEqual(result["action"], "ask_name")
        self.assertNotIn("contact", result)

    def test_longer_unregistered_name_is_not_truncated_to_known_name(self):
        for spoken in ("森川まさひろしさんに荷物です", "もりかわまさひろしさんに荷物です",
                       "森川将大郎さんに荷物です"):
            with self.subTest(spoken=spoken):
                result = self.flow.advance(spoken)
                self.assertNotEqual(result["action"], "guide")
                self.assertNotIn("contact", result)

    def test_wrong_reading_recovers_by_printed_kanji(self):
        start = self.flow.advance("森川しょうたさんに荷物です")
        self.assertEqual(start["action"], "ask_name")
        self.assertNotIn("contact", start)
        ready = self.flow.advance("読み方が分からないので、この宛名を読んでください", case_id=start["case_id"])
        self.assertEqual(ready["action"], "prepare_camera")
        pending = self.photo(start["case_id"], staff_name="森川将大")
        result = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=pending["confirmation_token"])
        self.assertEqual(result["contact"]["extension"], "8102")

    def test_given_name_answer_resolves_only_current_surname_candidates(self):
        for answer in ("将大です", "下の名前は将大です", "まさひろです"):
            with self.subTest(answer=answer):
                start = self.flow.advance("森川さんに荷物です")
                result = self.flow.advance(answer, case_id=start["case_id"])
                self.assertEqual(result["action"], "guide")
                self.assertEqual(result["contact"]["staff_id"], "P011")

    def test_given_name_ambiguity_or_denial_does_not_select_recipient(self):
        for answer in ("将大か健一です", "将大ではありません", "将大かどうか分かりません"):
            with self.subTest(answer=answer):
                start = self.flow.advance("森川さんに荷物です")
                result = self.flow.advance(answer, case_id=start["case_id"])
                self.assertNotEqual(result["action"], "guide")
                self.assertNotIn("contact", result)

    def test_document_request_in_delivery_keeps_purpose_and_prepares_once(self):
        start = self.flow.advance("森川さんに荷物です")
        result = self.flow.advance("下の名前の読み方が分からないので、これを読んでください",
                                   case_id=start["case_id"])
        self.assertEqual(result["action"], "prepare_camera")
        self.assertEqual(result["case_summary"]["purpose"], "delivery")

    def test_unique_camera_name_gets_group_from_database_not_image(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        result = self.photo(start["case_id"], staff_name="森川将大")
        self.assertEqual(result["action"], "confirm_recipient")
        self.assertEqual(result["case_summary"]["group_name"], "企画調整グループ")
        self.assertEqual(result["case_summary"]["sources"]["group_name"], "database")
        self.assertNotIn("contact", result)

    def test_confirmation_uses_registered_reading_not_guessed_reading(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        result = self.photo(start["case_id"], staff_name="森川将大")
        self.assertEqual(result.get("recipient_reading"), "もりかわまさひろ")
        self.assertIn("もりかわまさひろ", result["message"])

    def test_logged_natural_confirmations_guide_without_repeating_confirmation(self):
        for answer in ("はい、それでいいと思います。", "合ってると思います。", "はい、合ってると思います。"):
            with self.subTest(answer=answer):
                start = self.flow.advance("配達です。この宛名を読んでください")
                pending = self.photo(start["case_id"], staff_name="森川将大")
                result = self.flow.advance(answer, case_id=start["case_id"],
                                           confirmation_token=pending["confirmation_token"])
                self.assertEqual(result["action"], "guide")
                self.assertEqual(result["contact"]["extension"], "8102")

    def test_uncertain_or_negative_replies_do_not_authorize_recipient(self):
        for answer in ("多分そうだと思います", "はい、違います", "合ってると思いますが別の人かもしれません",
                       "はい、合ってると思いますが別の人かもしれません"):
            with self.subTest(answer=answer):
                start = self.flow.advance("配達です。この宛名を読んでください")
                pending = self.photo(start["case_id"], staff_name="森川将大")
                result = self.flow.advance(answer, case_id=start["case_id"],
                                           confirmation_token=pending["confirmation_token"])
                self.assertNotEqual(result["action"], "guide")
                self.assertNotIn("contact", result)

    def test_extension_guidance_and_repeat_speak_individual_digits(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        repeated = self.flow.advance("内線番号をもう一度教えて", case_id=start["case_id"])
        for result in (start, repeated):
            self.assertEqual(result["contact"]["extension"], "8102")
            self.assertEqual(result["contact"].get("extension_reading"), "はち・いち・ぜろ・に")
            self.assertIn("はち・いち・ぜろ・に", result["message"])
            self.assertNotIn("8102", result["message"])
        self.assertNotIn("head_motion", repeated)

    def test_extension_reading_preserves_leading_zero_and_registered_digits(self):
        for extension, reading in (("034579", "ぜろ・さん・よん・ご・なな・きゅう"),
                                   ("620", "ろく・に・ぜろ")):
            with self.subTest(extension=extension):
                self.data["staff"]["P011"]["demo_extension"] = extension
                result = self.flow.advance("企画調整グループの森川将大さんに荷物です")
                self.assertEqual(result["contact"]["extension"], extension)
                self.assertIn(reading, result["message"])

    def test_unknown_name_can_be_filled_by_photo_and_database(self):
        start = self.flow.advance("荷物です")
        self.flow.advance("名前の読み方が分かりません", case_id=start["case_id"])
        result = self.photo(start["case_id"], staff_name="森川将大")
        self.assertEqual(result["action"], "confirm_recipient")

    def test_facility_misread_confirms_registered_site_and_recipient_once(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大", facility_name="みらい銀座センター")
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertIn("ここは『みらい技術センター』です", pending["message"])
        self.assertIn("もりかわまさひろ", pending["message"])
        self.assertNotIn("みらい銀座センターの", pending["message"])
        self.assertNotIn("contact", pending)
        self.assertEqual(pending["case_summary"]["facility_name"], "みらい銀座センター")
        guided = self.flow.advance("はい", case_id=start["case_id"],
                                   confirmation_token=pending["confirmation_token"])
        self.assertEqual(guided["action"], "guide")
        self.assertEqual(guided["contact"]["extension"], "8102")

    def test_facility_name_in_legacy_group_field_is_not_a_department(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大", group_name="みらい銀座センター")
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertEqual(pending["case_summary"]["group_name"], "企画調整グループ")
        self.assertEqual(pending["case_summary"]["facility_name"], "みらい銀座センター")
        self.assertIn("みらい技術センター", pending["message"])

    def test_unknown_department_is_not_silently_replaced_by_database(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大", group_name="未登録グループ")
        self.assertEqual(pending["action"], "ask_correction")
        self.assertEqual(pending["case_summary"]["group_name"], "未登録グループ")
        self.assertNotIn("contact", pending)

    def test_unknown_department_without_correction_hands_off(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大", group_name="未登録グループ")
        self.assertEqual(pending["action"], "ask_correction")
        result = self.flow.advance("所属はわかりません", case_id=start["case_id"])
        self.assertEqual(result["action"], "handoff")
        self.assertNotIn("contact", result)
        self.assertNotIn("head_motion", result)

    def test_denied_destination_never_returns_internal_contact(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大", facility_name="別のセンター")
        denied = self.flow.advance("ここではありません", case_id=start["case_id"],
                                   confirmation_token=pending["confirmation_token"])
        self.assertEqual(denied["action"], "handoff")
        for result in (denied, self.flow.advance("はい", case_id=start["case_id"],
                                                confirmation_token=pending["confirmation_token"])):
            self.assertNotIn("contact", result)
            self.assertNotIn("head_motion", result)

    def test_new_facility_observation_invalidates_old_confirmation_token(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        old = self.photo(start["case_id"], staff_name="森川将大", facility_name="みらい銀座センター")
        new = self.photo(start["case_id"], staff_name="森川将大", facility_name="みらい技術センター")
        self.assertNotEqual(old["confirmation_token"], new["confirmation_token"])
        stale = self.flow.advance("はい", case_id=start["case_id"],
                                  confirmation_token=old["confirmation_token"])
        self.assertEqual(stale["action"], "confirm_recipient")
        self.assertNotIn("contact", stale)

    def test_unreadable_retake_clears_old_facility_and_still_confirms_local_site(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        old = self.photo(start["case_id"], staff_name="森川将大", facility_name="別のセンター")
        new = self.photo(start["case_id"], staff_name="森川将大")
        self.assertEqual(new["case_summary"]["facility_name"], "")
        self.assertIn("みらい技術センター", new["message"])
        self.assertNotEqual(old["confirmation_token"], new["confirmation_token"])

    def test_read_only_document_does_not_confirm_visit_destination(self):
        start = self.flow.advance("この宛名を読んでください")
        result = self.photo(start["case_id"], staff_name="森川将大", facility_name="別のセンター")
        self.assertEqual(result["action"], "read_only")
        self.assertNotIn("confirmation_token", result)
        self.assertNotIn("contact", result)

    def test_facility_input_has_bounded_length(self):
        result = self.flow.advance("配達です", facility_name="あ" * 257)
        self.assertEqual(result["reason"], "input_too_long")

    def test_spoken_facility_argument_also_requires_destination_confirmation(self):
        result = self.flow.advance("みらい銀座センターの森川将大さんに荷物です",
                                   facility_name="みらい銀座センター")
        self.assertEqual(result["action"], "confirm_recipient")
        self.assertIn("みらい技術センター", result["message"])
        self.assertNotIn("contact", result)

    def test_bare_negative_requests_correction_instead_of_repeating_confirmation(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大")
        denied = self.flow.advance("いいえ", case_id=start["case_id"],
                                   confirmation_token=pending["confirmation_token"])
        self.assertEqual(denied["action"], "ask_correction")
        self.assertNotIn("contact", denied)

    def test_destination_denial_after_guidance_revokes_contact_without_token(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大", facility_name="みらい技術センター")
        guide = self.flow.advance("はい", case_id=start["case_id"],
                                  confirmation_token=pending["confirmation_token"])
        self.assertEqual(guide["action"], "guide")
        self.assertEqual(guide["case_summary"]["confirmed_facility_name"], "みらい技術センター")
        denied = self.flow.advance("ここではありません", case_id=start["case_id"])
        self.assertEqual(denied["action"], "handoff")
        repeated = self.flow.advance("内線番号をもう一度", case_id=start["case_id"])
        for result in (denied, repeated):
            self.assertNotIn("contact", result)
            self.assertNotIn("head_motion", result)
            self.assertEqual(result["case_summary"]["confirmed_facility_name"], "")

    def test_partial_camera_name_does_not_choose_first_person(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        result = self.photo(start["case_id"], staff_name="森川")
        self.assertEqual(result["action"], "ask_name")
        self.assertNotIn("contact", result)

    def test_multiple_affiliations_do_not_choose_first_group(self):
        self.data["staff"]["P011"]["group_ids"] = ["G01", "G02"]
        start = self.flow.advance("配達です。この宛名を読んでください")
        result = self.photo(start["case_id"], staff_name="森川将大")
        self.assertEqual(result["action"], "ask_group")
        self.assertEqual(result["case_summary"]["group_name"], "")

    def test_confirmed_photo_returns_registered_extension_and_right_motion(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大")
        self.assertEqual(pending["action"], "confirm_recipient")
        result = self.flow.advance("はい", case_id=start["case_id"],
                                   confirmation_token=pending["confirmation_token"])
        self.assertEqual(result["action"], "guide")
        self.assertEqual(result["contact"]["extension"], "8102")
        self.assertFalse(result["contact"]["automatic_dial"])
        self.assertIn("はち・いち・ぜろ・に", result["message"])
        self.assertEqual(result["head_motion"]["point"]["yaw"], -45)
        self.assertFalse(result["reception_completed"])

    def test_number_repeat_does_not_repeat_body_motion(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.assertEqual(start["action"], "guide")
        repeated = self.flow.advance("内線番号をもう一度教えて", case_id=start["case_id"])
        self.assertEqual(repeated["action"], "repeat_contact")
        self.assertEqual(repeated["contact"]["extension"], "8102")
        self.assertNotIn("head_motion", repeated)
        thanks = self.flow.advance("ありがとう", case_id=start["case_id"])
        self.assertEqual(thanks["action"], "done")

    def test_invalid_extension_never_guides_or_points(self):
        for invalid in (None, "", "81-02", "call someone"):
            with self.subTest(extension=invalid):
                self.data["staff"]["P011"]["demo_extension"] = invalid
                result = app.route_result(self.data, "delivery_contact", request_type="delivery",
                                          staff_id="P011", staff_name="森川将大")
                self.assertFalse(result["success"])
                self.assertEqual(result["reason"], "extension_unregistered")
                self.assertNotIn("head_motion", result)

    def test_reported_phone_destination_preserves_delivery_and_avoids_recheckin(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        result = self.flow.advance("電話で研修棟の入口で受け取ると言われたんですが、どこですか",
                                   case_id=start["case_id"])
        self.assertEqual(result["action"], "guide")
        self.assertEqual(result["guidance_context"], "delivery_meetup")
        self.assertEqual(result["case_summary"]["purpose"], "delivery")
        self.assertTrue(result["case_summary"]["recipient_confirmed"])
        self.assertIn("研修棟", result["message"])
        self.assertNotIn("受付", result["message"])
        self.assertEqual(result["head_motion"]["point"]["yaw"], -45)
        self.assertFalse(result["reception_completed"])
        repeated_number = self.flow.advance("内線番号をもう一度", case_id=start["case_id"])
        self.assertEqual(repeated_number["contact"]["extension"], "8102")

    def test_repeated_meetup_direction_does_not_repeat_motion(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.flow.advance("電話で研修棟に来てと言われました", case_id=start["case_id"])
        result = self.flow.advance("研修棟はどこでしたっけ", case_id=start["case_id"])
        self.assertEqual(result["action"], "repeat_guidance")
        self.assertNotIn("head_motion", result)
        self.assertNotIn("受付", result["message"])

    def test_meetup_entry_procedure_is_not_invented(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.flow.advance("電話で研修棟の入口で受け取ると言われました", case_id=start["case_id"])
        result = self.flow.advance("受付とかやるんですか", case_id=start["case_id"])
        self.assertEqual(result["action"], "explain_delivery")
        self.assertIn("宛先の照合と内線の案内", result["message"])
        self.assertNotIn("受付不要", result["message"])
        self.assertNotIn("受付でご用件", result["message"])
        self.assertNotIn("head_motion", result)

    def test_plain_building_question_does_not_claim_a_phone_agreement(self):
        result = app.get_facility_guidance("研修棟")
        self.assertTrue(result["success"])
        self.assertIn("受付", result["message"])
        self.assertNotIn("guidance_context", result)

    def test_unconfirmed_recipient_cannot_use_delivery_meetup_branch(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        self.photo(start["case_id"], staff_name="森川将大")
        result = self.flow.advance("電話で研修棟に来てと言われました", case_id=start["case_id"])
        self.assertNotEqual(result.get("guidance_context"), "delivery_meetup")
        self.assertNotIn("head_motion", result)

    def test_meetup_does_not_fabricate_unknown_or_external_destination(self):
        for utterance in ("電話で未登録倉庫に来てと言われました", "電話で地域防災連絡所に来てと言われました"):
            with self.subTest(utterance=utterance):
                start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
                result = self.flow.advance(utterance, case_id=start["case_id"])
                self.assertNotEqual(result.get("guidance_context"), "delivery_meetup")

    def test_recipient_or_purpose_correction_does_not_reuse_meetup(self):
        for utterance in ("電話で佐藤花子さんが研修棟に来てと言いました", "荷物ではなく会議です。電話で研修棟に来てと言われました"):
            with self.subTest(utterance=utterance):
                start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
                result = self.flow.advance(utterance, case_id=start["case_id"])
                self.assertNotEqual(result.get("guidance_context"), "delivery_meetup")

    def test_denied_meetup_is_not_treated_as_an_agreement(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        result = self.flow.advance("電話では研修棟に来てとは言われていません", case_id=start["case_id"])
        self.assertNotEqual(result.get("guidance_context"), "delivery_meetup")

    def test_reading_only_request_does_not_reuse_meetup_direction(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.flow.advance("電話で研修棟に来てと言われました", case_id=start["case_id"])
        result = self.flow.advance("研修棟の資料を読んでください。文字だけでいいです", case_id=start["case_id"])
        self.assertEqual(result["action"], "read_only")
        self.assertNotIn("head_motion", result)

    def test_negative_phone_instruction_clears_agreed_destination(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.flow.advance("電話で研修棟に来てと言われました", case_id=start["case_id"])
        result = self.flow.advance("電話で研修棟に来ないでと言われました", case_id=start["case_id"])
        self.assertNotEqual(result["action"], "guide")
        self.assertNotIn("head_motion", result)
        self.assertEqual(result["case_summary"]["meetup_facility"], "")

    def test_named_landmark_is_not_an_unregistered_meetup_destination(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        result = self.flow.advance("研修棟の近くの未登録倉庫に来てと言われました", case_id=start["case_id"])
        self.assertNotEqual(result.get("guidance_context"), "delivery_meetup")
        self.assertNotIn("head_motion", result)

    def test_spoken_group_correction_precedes_meetup_guidance(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        result = self.flow.advance("宛先の所属は計測技術グループです。電話で研修棟に来てと言われました",
                                   case_id=start["case_id"])
        self.assertNotEqual(result.get("guidance_context"), "delivery_meetup")
        self.assertEqual(result["case_summary"]["group_name"], "計測技術グループ")
        self.assertFalse(result["case_summary"]["recipient_confirmed"])

    def test_new_training_visit_is_not_a_delivery_meetup(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        result = self.flow.advance("配達は終わりました。今度は研修を受けに来ました。電話で研修棟に来てと言われました",
                                   case_id=start["case_id"])
        self.assertNotEqual(result.get("guidance_context"), "delivery_meetup")
        self.assertNotEqual(result["case_summary"]["purpose"], "delivery")

    def test_named_meetup_entry_question_gets_unknown_policy_explanation(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.flow.advance("電話で研修棟に来てと言われました", case_id=start["case_id"])
        result = self.flow.advance("研修棟の受付は必要ですか", case_id=start["case_id"])
        self.assertEqual(result["action"], "explain_delivery")
        self.assertIn("入館手続きの要否は確認できていません", result["message"])
        self.assertNotIn("head_motion", result)

    def test_changed_unknown_destination_revokes_previous_meetup(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.flow.advance("電話で研修棟に来てと言われました", case_id=start["case_id"])
        changed = self.flow.advance("受渡し場所は未登録倉庫に変更と言われました", case_id=start["case_id"])
        self.assertEqual(changed["action"], "ask_correction")
        self.assertEqual(changed["case_summary"]["meetup_facility"], "")
        self.assertNotIn("head_motion", changed)
        old = self.flow.advance("研修棟で受け取るんでしたっけ", case_id=start["case_id"])
        self.assertNotEqual(old.get("guidance_context"), "delivery_meetup")
        self.assertNotIn("head_motion", old)

    def test_logged_colloquial_unknown_offers_document_without_claiming_preview(self):
        start = self.flow.advance("森川さんにお荷物なんですけど")
        result = self.flow.advance("所属わかんなくて", case_id=start["case_id"])
        self.assertEqual(result["action"], "offer_document")
        self.assertNotIn("緑枠", result["message"])
        self.assertNotIn("head_motion", result)

    def test_unknown_group_with_known_given_name_does_not_force_camera(self):
        start = self.flow.advance("森川さんに荷物です")
        result = self.flow.advance("所属はわかんなくて、下の名前は将大です", case_id=start["case_id"])
        self.assertNotEqual(result["action"], "offer_document")

    def test_logged_colloquial_phone_destination_keeps_case(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        result = self.flow.advance("研修棟ってところにこ直接来てくれって言われたんですけど、どこですかね",
                                   case_id=start["case_id"])
        self.assertEqual(result["action"], "guide")
        self.assertEqual(result["guidance_context"], "delivery_meetup")
        self.assertNotIn("受付", result["message"])
        self.assertEqual(result["case_summary"]["purpose"], "delivery")

    def test_colloquial_landmark_is_not_the_unknown_actual_destination(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        result = self.flow.advance("研修棟ってところの近くの未登録倉庫に来てと言われました", case_id=start["case_id"])
        self.assertNotEqual(result.get("guidance_context"), "delivery_meetup")
        self.assertNotIn("head_motion", result)

    def test_partial_number_question_returns_only_digit_reading(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        result = self.flow.advance("810なん？", case_id=start["case_id"])
        self.assertEqual(result["action"], "repeat_contact")
        self.assertIn("はち・いち・ぜろ・に", result["message"])
        self.assertNotIn("8102", result["message"])
        self.assertNotIn("head_motion", result)

    def test_reading_question_preserves_camera_identity_and_explains_directory_reading(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.photo(start["case_id"], staff_name="森川将大")
        result = self.flow.advance("グループがわからないんですけど、下の名前もこれ正裕でいいんですかね",
                                   case_id=start["case_id"])
        self.assertEqual(result["action"], "confirm_recipient")
        self.assertEqual(result["case_summary"]["staff_name"], "森川将大")
        self.assertEqual(result["confirmation_token"], pending["confirmation_token"])
        self.assertIn("登録読み", result["message"])
        self.assertIn("もりかわまさひろ", result["message"])
        self.assertNotIn("contact", result)

    def test_standalone_building_question_can_use_reception_entry(self):
        result = app.advance_reception("研修棟はどこですか")
        self.assertEqual(result["action"], "guide")
        self.assertIn("研修棟", result["message"])
        self.assertEqual(result["head_motion"]["point"]["yaw"], -45)

    def test_standalone_workplace_question_can_use_reception_entry(self):
        result = app.advance_reception("計測技術グループの斎藤一郎さんの通常の勤務場所を教えて")
        self.assertIn("登録", result["message"])
        self.assertFalse(result["reception_completed"])

    def test_direct_recipient_reference_reuses_reported_meetup_not_workplace(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.flow.advance("研修棟ってところに直接来てくれって言われたんですけど、どこですかね",
                          case_id=start["case_id"])
        result = self.flow.advance("森川さんのところに直接来いって言われたんですけど", case_id=start["case_id"])
        self.assertEqual(result["action"], "repeat_guidance")
        self.assertEqual(result["case_summary"]["staff_name"], "森川将大")
        self.assertIn("研修棟", result["message"])
        self.assertNotIn("受付", result["message"])
        self.assertNotIn("head_motion", result)

    def test_reading_question_never_overrides_purpose_correction(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        old = self.photo(start["case_id"], staff_name="森川将大")
        changed = self.flow.advance("配達ではなく面会です。読み方はどうですか", case_id=start["case_id"])
        self.assertEqual(changed["case_summary"]["purpose"], "meeting")
        self.assertNotEqual(changed["confirmation_token"], old["confirmation_token"])
        stale = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=old["confirmation_token"])
        self.assertNotEqual(stale["action"], "guide")
        self.assertNotIn("head_motion", stale)

    def test_gateway_does_not_guide_to_explicitly_denied_building(self):
        result = app.advance_reception("研修棟ではなく未登録館に行きたい")
        self.assertNotEqual(result["action"], "guide")
        self.assertNotIn("head_motion", result)

    def test_gateway_does_not_guide_after_negative_movement_statement(self):
        for utterance in ("研修棟へは行きません", "研修棟には行かないでと言われました"):
            with self.subTest(utterance=utterance):
                result = app.advance_reception(utterance)
                self.assertNotEqual(result["action"], "guide")
                self.assertNotIn("head_motion", result)

    def test_gateway_preserves_workplace_question_across_group_answer(self):
        start = app.advance_reception("斎藤さんの勤務場所を教えて")
        self.assertEqual(start["action"], "ask_correction")
        result = app.advance_reception("計測技術グループです", case_id=start["case_id"])
        self.assertEqual(result["action"], "guide")
        self.assertNotEqual(result["case_summary"]["purpose"], "delivery")

    def test_gateway_unknown_workplace_group_hands_off_instead_of_looping(self):
        start = app.advance_reception("斎藤さんの勤務場所を教えて")
        result = app.advance_reception("所属はわかりません", case_id=start["case_id"])
        self.assertEqual(result["action"], "handoff")
        self.assertNotIn("head_motion", result)

    def test_new_registered_name_with_reading_question_invalidates_old_confirmation(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        old = self.photo(start["case_id"], staff_name="森川将大")
        changed = self.flow.advance("森川健一さんです。下の名前はどう読むんですか", case_id=start["case_id"])
        self.assertEqual(changed["case_summary"]["staff_name"], "森川健一")
        self.assertNotEqual(changed["confirmation_token"], old["confirmation_token"])

    def test_destination_denial_with_reading_question_never_retains_old_consent(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        old = self.photo(start["case_id"], staff_name="森川将大")
        result = self.flow.advance("ここではありません。読み方はどうですか", case_id=start["case_id"])
        self.assertEqual(result["action"], "handoff")
        stale = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=old["confirmation_token"])
        self.assertNotIn("contact", stale)

    def test_logged_confirmation_fillers_and_positive_phrases_are_accepted_once(self):
        for answer in ("あ、はい、そうです。", "いいです。", "はい、いいです。", "えっと、はい、合ってます。"):
            with self.subTest(answer=answer):
                start = self.flow.advance("配達です。この宛名を読んでください")
                pending = self.photo(start["case_id"], staff_name="森川将大")
                result = self.flow.advance(answer, case_id=start["case_id"], confirmation_token=pending["confirmation_token"])
                self.assertEqual(result["action"], "guide")
                self.assertEqual(result["contact"]["extension"], "8102")

    def test_confirmation_filler_normalization_never_strips_denial_or_token_requirement(self):
        for answer in ("あ、はい、違います", "もういいです", "いいですけど違う人です", "ないです"):
            with self.subTest(answer=answer):
                start = self.flow.advance("配達です。この宛名を読んでください")
                pending = self.photo(start["case_id"], staff_name="森川将大")
                result = self.flow.advance(answer, case_id=start["case_id"], confirmation_token=pending["confirmation_token"])
                self.assertNotEqual(result["action"], "guide")
                self.assertNotIn("contact", result)
        start = self.flow.advance("配達です。この宛名を読んでください")
        self.photo(start["case_id"], staff_name="森川将大")
        self.assertNotEqual(self.flow.advance("あ、はい、そうです", case_id=start["case_id"])["action"], "guide")

    def test_camera_result_never_replays_old_document_offer_as_new_voice(self):
        start = self.flow.advance("森川さんに荷物です")
        offered = self.flow.advance("所属わかんなくて", case_id=start["case_id"])
        self.assertEqual(offered["action"], "offer_document")
        result = self.flow.advance("あはい、宛名ラベルがありますね", case_id=start["case_id"],
                                   source="camera", staff_name="森川将大", facility_name="みらい技術センター")
        self.assertEqual(result["action"], "confirm_recipient")
        self.assertEqual(result["case_summary"]["purpose"], "delivery")
        self.assertNotIn("next_tool", result)

    def test_camera_result_cannot_change_purpose_using_attached_utterance(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        result = self.flow.advance("面会です", case_id=start["case_id"], source="camera", staff_name="森川将大")
        self.assertEqual(result["case_summary"]["purpose"], "delivery")
        self.assertEqual(result["action"], "confirm_recipient")

    def test_normal_questions_and_confirmation_are_successful_waiting_states(self):
        start = self.flow.advance("森川さんに荷物です")
        offer = self.flow.advance("所属わかんなくて", case_id=start["case_id"])
        preview = self.flow.advance("はい、あります", case_id=start["case_id"])
        confirm = self.photo(start["case_id"], staff_name="森川将大")
        for result, step in ((start, "ask_and_wait"), (offer, "ask_and_wait"),
                             (preview, "start_preview_and_wait"), (confirm, "ask_and_wait")):
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "waiting")
            self.assertEqual(result["next_step"], step)
            self.assertEqual(result["needs_clarification"], step == "ask_and_wait")
            self.assertFalse(result["guidance_ready"])
            self.assertNotIn("head_motion", result)

    def test_guidance_answer_and_done_have_distinct_next_steps(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.assertTrue(start["success"])
        self.assertTrue(start["guidance_ready"])
        self.assertEqual((start["status"], start["next_step"]), ("ready", "guide_and_end"))
        self.assertFalse(start["reception_completed"])
        repeated = self.flow.advance("810なん？", case_id=start["case_id"])
        self.assertTrue(repeated["success"])
        self.assertFalse(repeated["guidance_ready"])
        self.assertEqual((repeated["status"], repeated["next_step"]), ("answered", "say_and_end"))
        self.assertNotIn("head_motion", repeated)
        done = self.flow.advance("ありがとう", case_id=start["case_id"])
        self.assertTrue(done["success"])
        self.assertEqual((done["status"], done["next_step"]), ("done", "end"))

    def test_handoff_is_a_normal_workflow_result_not_a_processing_error(self):
        start = self.flow.advance("森川さんに荷物です")
        self.flow.advance("所属わかんなくて", case_id=start["case_id"])
        result = self.flow.advance("書類はありません", case_id=start["case_id"])
        self.assertEqual(result["action"], "handoff")
        self.assertTrue(result["success"])
        self.assertEqual((result["status"], result["next_step"]), ("handoff", "handoff_and_end"))
        self.assertFalse(result["guidance_ready"])
        self.assertNotIn("head_motion", result)

    def test_input_and_expired_case_errors_are_not_normal_waiting_success(self):
        for kwargs in ({"source": "invalid"}, {"user_utterance": "あ" * 4001}, {"case_id": "unknown-case"}):
            with self.subTest(kind=next(iter(kwargs))):
                request = {"user_utterance": "配達です", **kwargs}
                result = self.flow.advance(**request)
                self.assertFalse(result["success"])
                self.assertEqual(result["status"], "error")
                self.assertFalse(result["guidance_ready"])
                self.assertNotIn("head_motion", result)

    def test_readonly_and_training_routing_are_explicit_without_motion(self):
        reading = self.flow.advance("この書類を読んでください")
        self.assertEqual((reading["status"], reading["next_step"]), ("answered", "read_and_end"))
        training = self.flow.advance("研修に参加です")
        self.assertTrue(training["success"])
        self.assertEqual((training["status"], training["next_step"]), ("routing", "call_next_tool"))
        self.assertFalse(training["guidance_ready"])
        self.assertNotIn("head_motion", training)

    def test_failed_lookup_cannot_leak_motion_when_question_processing_succeeds(self):
        self.flow.general_guidance = lambda data, utterance, pending: {
            "success": False, "message": "所属を教えてください。",
            "head_motion": {"point": {"yaw": 45, "pitch": 0, "speed": 500}}}
        result = self.flow.advance("勤務場所を教えて")
        self.assertEqual(result["action"], "ask_correction")
        self.assertTrue(result["success"])
        self.assertFalse(result["guidance_ready"])
        self.assertEqual(result["next_step"], "ask_and_wait")
        self.assertNotIn("head_motion", result)

    def test_explicit_reading_only_can_replace_delivery(self):
        for request in ("受付は不要です。読み取りだけお願いします。これを読んでください",
                        "これを読んでください。文字を読むだけでいいです",
                        "荷物ではありません。文字だけ読んでください"):
            with self.subTest(request=request):
                start = self.flow.advance("森川さんに荷物です")
                result = self.flow.advance(request, case_id=start["case_id"])
                self.assertEqual(result["action"], "read_only")
                photo = self.photo(start["case_id"], staff_name="森川将大")
                self.assertEqual(photo["action"], "read_only")
                self.assertNotIn("contact", photo)


    def test_logged_camera_given_name_uses_voice_surname_and_confirms_once(self):
        start = self.flow.advance("森川さんにお荷物です。")
        pending = self.flow.advance("はいとって。", case_id=start["case_id"], source="camera",
                                    staff_name="将大", document_type="parcel_label")
        self.assertEqual(pending["action"], "confirm_recipient")
        summary = pending["case_summary"]
        self.assertEqual(summary["staff_name"], "森川将大")
        self.assertEqual(summary["sources"]["staff_name"], "voice+camera")
        self.assertEqual(summary["name_evidence"], {
            "voice_family_name": "森川", "camera_given_name": "将大", "matched_staff_name": "森川将大"})
        self.assertIn("候補", pending["message"])
        self.assertIn("森川将大", pending["message"])
        self.assertEqual(pending["message"].count("？"), 1)
        self.assertNotIn("head_motion", pending)
        self.assertNotIn("contact", pending)
        no_token = self.flow.advance("はい", case_id=start["case_id"])
        self.assertEqual(no_token["action"], "confirm_recipient")
        self.assertEqual(no_token["confirmation_token"], pending["confirmation_token"])
        guided = self.flow.advance("あ、はい、そうです", case_id=start["case_id"],
                                   confirmation_token=pending["confirmation_token"])
        self.assertEqual(guided["action"], "guide")
        self.assertEqual(guided["contact"]["extension"], "8102")
        self.assertEqual(guided["head_motion"]["point"]["yaw"], -45)

    def test_camera_given_name_does_not_borrow_surname_from_another_case(self):
        self.flow.advance("森川さんに荷物です")
        separate = self.flow.advance("荷物です")
        result = self.photo(separate["case_id"], staff_name="将大")
        self.assertNotEqual(result["action"], "confirm_recipient")
        self.assertNotEqual(result["case_summary"]["staff_name"], "森川将大")
        self.assertNotIn("contact", result)

    def test_camera_given_name_never_overrides_a_different_spoken_full_name(self):
        start = self.flow.advance("森川健一さんに荷物です")
        result = self.photo(start["case_id"], staff_name="将大")
        self.assertEqual(result["action"], "ask_correction")
        self.assertEqual(result["case_summary"]["staff_name"], "森川健一")
        self.assertNotIn("contact", result)

    def test_agreeing_photo_preserves_explicit_spoken_full_name_against_later_conflict(self):
        start = self.flow.advance("森川健一さんへの配達です。この宛名を読んでください")
        agreed = self.photo(start["case_id"], staff_name="森川健一")
        self.assertEqual(agreed["case_summary"]["sources"]["staff_name"], "voice")
        conflict = self.photo(start["case_id"], staff_name="森川将大")
        self.assertEqual(conflict["action"], "ask_correction")
        self.assertEqual(conflict["case_summary"]["staff_name"], "森川健一")
        self.assertNotIn("contact", conflict)

    def test_explicit_voice_full_name_strengthens_existing_partial_name_evidence(self):
        start = self.flow.advance("森川さんに荷物です")
        self.photo(start["case_id"], staff_name="将大")
        spoken = self.flow.advance("森川将大さんです", case_id=start["case_id"], source="voice_correction")
        self.assertEqual(spoken["case_summary"]["sources"]["staff_name"], "voice_correction")
        self.assertNotIn("name_evidence", spoken["case_summary"])
        conflict = self.photo(start["case_id"], staff_name="健一")
        self.assertEqual(conflict["action"], "ask_correction")
        self.assertEqual(conflict["case_summary"]["staff_name"], "森川将大")
        self.assertNotIn("contact", conflict)

    def test_camera_given_name_requires_complete_registered_given_name(self):
        for value in ("将", "正代", "将大郎"):
            with self.subTest(value=value):
                start = self.flow.advance("森川さんに荷物です")
                result = self.photo(start["case_id"], staff_name=value)
                self.assertEqual(result["action"], "ask_correction")
                self.assertNotIn("name_evidence", result["case_summary"])
                self.assertNotIn("contact", result)

    def test_camera_given_name_does_not_choose_between_homophonous_full_names(self):
        self.data["staff"]["P012"].update(family_name="守川", given_name="将大", reading="もりかわまさひろ")
        start = self.flow.advance("もりかわさんに荷物です")
        result = self.photo(start["case_id"], staff_name="将大")
        self.assertNotEqual(result["action"], "confirm_recipient")
        self.assertNotIn("name_evidence", result["case_summary"])
        self.assertNotIn("contact", result)

    def test_empty_retake_drops_previous_camera_given_name_but_keeps_voice_surname(self):
        start = self.flow.advance("森川さんに荷物です")
        old = self.photo(start["case_id"], staff_name="将大")
        self.assertEqual(old["action"], "confirm_recipient")
        blank = self.photo(start["case_id"], staff_name="")
        self.assertEqual(blank["case_summary"]["staff_name"], "森川")
        self.assertNotIn("name_evidence", blank["case_summary"])
        stale = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=old["confirmation_token"])
        self.assertNotEqual(stale["action"], "guide")
        self.assertNotIn("head_motion", stale)

    def test_given_name_retake_replaces_evidence_and_rejects_old_token(self):
        start = self.flow.advance("森川さんに荷物です")
        old = self.photo(start["case_id"], staff_name="将大")
        self.assertEqual(old["action"], "confirm_recipient")
        changed = self.photo(start["case_id"], staff_name="健一")
        self.assertEqual(changed["action"], "confirm_recipient")
        self.assertEqual(changed["case_summary"]["staff_name"], "森川健一")
        self.assertEqual(changed["case_summary"]["name_evidence"]["camera_given_name"], "健一")
        stale = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=old["confirmation_token"])
        self.assertNotEqual(stale["action"], "guide")
        guided = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=changed["confirmation_token"])
        self.assertEqual(guided["contact"]["extension"], "8202")

    def test_full_name_retake_clears_partial_name_evidence(self):
        start = self.flow.advance("森川さんに荷物です")
        self.photo(start["case_id"], staff_name="将大")
        full = self.photo(start["case_id"], staff_name="森川将大")
        self.assertEqual(full["action"], "confirm_recipient")
        self.assertEqual(full["case_summary"]["sources"]["staff_name"], "camera")
        self.assertNotIn("name_evidence", full["case_summary"])

    def test_logged_unreadable_name_answers_offer_document_without_repeating_name(self):
        for utterance in ("下の名前がちょっとこれ漢字読めないんですよね。",
                          "下の名前は分かるんですけど、読み方がちょっと。"):
            with self.subTest(utterance=utterance):
                start = self.flow.advance("森川さんにお荷物です")
                result = self.flow.advance(utterance, case_id=start["case_id"])
                self.assertEqual(result["action"], "offer_document")
                self.assertNotIn("head_motion", result)
                ready = self.flow.advance("あ、はい、どうぞ。", case_id=start["case_id"])
                self.assertEqual(ready["action"], "prepare_camera")


class InformationRequestTests(unittest.TestCase):
    def setUp(self):
        from reception_flow import ReceptionFlow
        self.data = copy.deepcopy(app.load_data())
        self.loader = patch.object(app, "load_data", return_value=self.data)
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.flow = ReceptionFlow(lambda: self.data, app._document_recipient_guidance,
                                  app.staff_candidates, app.matched_group_ids, app.evidence_staff_query,
                                  general_guidance=app._general_reception_guidance,
                                  delivery_destination=app._delivery_meetup_guidance)
        self.flow.contact_lookup = getattr(app, "_contact_guidance", None)

    def document_question(self, text, **kwargs):
        import inspect
        self.assertIn("document_context", inspect.signature(self.flow.advance).parameters)
        return self.flow.advance(text, **kwargs)

    def test_read_only_photo_can_later_supply_contact_recipient(self):
        start = self.flow.advance("この書類を読んでください")
        read = self.flow.advance("", case_id=start["case_id"], source="camera",
                                 staff_name="森川将大", facility_name="みらい技術センター")
        self.assertEqual(read["action"], "read_only")
        pending = self.flow.advance("この人の内線は？", case_id=start["case_id"])
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertNotIn("contact", pending)
        answer = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=pending["confirmation_token"])
        self.assertEqual(answer["contact"]["extension"], "8102")

    def test_logged_traditional_kanji_photo_uses_registered_name_alias(self):
        for spoken in ("森川さんにお荷物です", "森川将大さんにお荷物です"):
            with self.subTest(spoken=spoken):
                start = self.flow.advance(spoken)
                pending = self.flow.advance("", case_id=start["case_id"], source="camera",
                                            staff_name="森川將大", facility_name="みらい技術センター")
                self.assertEqual(pending["action"], "confirm_recipient")
                self.assertEqual(pending["case_summary"]["staff_name"], "森川將大")
                self.assertNotIn("contact", pending)
                answer = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=pending["confirmation_token"])
                self.assertEqual(answer["contact"]["staff_name"], "森川将大")
                self.assertEqual(answer["contact"]["extension"], "8102")

    def test_name_alias_does_not_enable_unregistered_similar_name(self):
        start = self.flow.advance("森川さんに荷物です")
        r = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川将太")
        self.assertEqual(r["action"], "ask_correction")
        self.assertNotIn("contact", r)

    def test_equivalent_alias_photo_does_not_erase_spoken_full_identity(self):
        start = self.flow.advance("森川将大さんへの配達です。この宛名を読んでください")
        same = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川將大")
        self.assertEqual(same["action"], "confirm_recipient")
        self.assertEqual(same["case_summary"]["staff_name"], "森川將大")
        self.assertEqual(same["case_summary"].get("voice_full_name"), "森川将大")
        conflict = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川健一")
        self.assertEqual(conflict["action"], "ask_correction")
        self.assertNotIn("contact", conflict)

    def test_explicit_spoken_correction_replaces_full_identity_evidence(self):
        start = self.flow.advance("森川将大さんに配達です。この宛名を読んでください")
        self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川將大")
        changed = self.flow.advance("森川健一さんです", case_id=start["case_id"], source="voice_correction")
        self.assertEqual(changed["case_summary"].get("voice_full_name"), "森川健一")
        photo = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川健一")
        self.assertEqual(photo["action"], "confirm_recipient")

    def test_blank_alias_retake_keeps_voice_identity_and_rejects_different_person(self):
        start = self.flow.advance("森川将大さんに配達です。この宛名を読んでください")
        self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川將大")
        blank = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="")
        self.assertEqual(blank["case_summary"]["staff_name"], "森川将大")
        self.assertEqual(blank["case_summary"]["sources"]["staff_name"], "voice")
        conflict = self.flow.advance("", case_id=start["case_id"], source="camera",
                                     staff_name="森川健一", group_name="計測技術グループ")
        self.assertEqual(conflict["action"], "ask_correction")
        self.assertNotIn("contact", conflict)

    def test_duplicate_name_alias_requires_disambiguation(self):
        self.data["staff"]["P012"]["name_aliases"] = ["森川將大"]
        start = self.flow.advance("森川さんに荷物です")
        r = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川將大")
        self.assertEqual(r["action"], "ask_group")
        self.assertNotIn("contact", r)
        self.assertNotIn("head_motion", r)

    def test_full_name_alias_obeys_voice_name_boundaries(self):
        self.assertEqual(app.evidence_staff_query(self.data, "森川將大さんの内線は？"), "森川將大")
        self.assertNotEqual(app.evidence_staff_query(self.data, "森川將大郎さんの内線は？"), "森川將大")

    def test_workplace_alias_uses_same_registered_person(self):
        from facility_engine import FacilityGuide
        g = FacilityGuide.from_files(staff_data=self.data)
        canonical = g.staff_workplace("森川将大", "企画調整グループ")
        alias = g.staff_workplace("森川將大", "企画調整グループ")
        self.assertEqual(alias, canonical)

    def test_unrecorded_latest_photo_is_explicitly_transferred_as_camera_context(self):
        pending = self.document_question("はい、この人の内戦は？", staff_name="森川将大",
            document_context={"staff_name": "森川将大", "facility_name": "みらい技術センター"})
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertEqual(pending["case_summary"]["sources"]["staff_name"], "camera")
        self.assertEqual(pending["case_summary"]["information_request"], "contact")
        self.assertNotIn("contact", pending)
        no_token = self.flow.advance("はい", case_id=pending["case_id"])
        self.assertEqual(no_token["action"], "confirm_recipient")
        answer = self.flow.advance("はい", case_id=pending["case_id"], confirmation_token=pending["confirmation_token"])
        self.assertEqual(answer["contact"]["extension"], "8102")

    def test_deictic_homophone_without_image_asks_only_recipient(self):
        r = self.flow.advance("はい、この人の内戦は？")
        self.assertEqual(r["action"], "ask_name")
        self.assertNotIn("contact", r)

    def test_political_civil_war_is_not_a_contact_request(self):
        r = self.flow.advance("この国の内戦はいつ終わりますか")
        self.assertNotEqual(r["case_summary"]["information_request"], "contact")
        self.assertNotIn("contact", r)

    def test_document_context_requires_explicit_image_person_reference(self):
        r = self.document_question("内線を教えて", document_context={"staff_name": "森川将大"})
        self.assertFalse(r["success"])
        self.assertEqual(r["reason"], "invalid_document_context")

    def test_document_context_validates_fields_before_creating_case(self):
        for doc in ({"staff_name": 123}, {"staff_name": "将" * 257}, {"staff_id": "P011"}):
            with self.subTest(doc=doc):
                before = len(self.flow._cases)
                r = self.document_question("この人の内線は？", document_context=doc)
                self.assertEqual(r["reason"], "invalid_document_context")
                self.assertEqual(len(self.flow._cases), before)

    def test_document_context_cannot_replay_voice_as_camera_input(self):
        r = self.document_question("この人の内線は？", source="camera",
                                   document_context={"staff_name": "森川将大"})
        self.assertFalse(r["success"])

    def test_explicit_different_recipient_does_not_use_photo_person(self):
        r = self.document_question("この人ではなく、森川健一さんの内線を教えて", staff_name="森川健一",
                                   document_context={"staff_name": "森川将大"})
        self.assertEqual(r["case_summary"]["staff_name"], "森川健一")
        self.assertNotEqual(r.get("contact", {}).get("extension"), "8102")

    def test_number_question_asks_person_not_visit_purpose(self):
        r = self.flow.advance("内線電話番号わかりますか？")
        self.assertEqual(r["action"], "ask_name")
        self.assertEqual(r["case_summary"]["information_request"], "contact")
        self.assertIn("内線", r["message"])

    def test_contact_request_survives_meeting_then_group(self):
        start = self.flow.advance("内線電話番号わかりますか？")
        missing = self.flow.advance("森川さんと会議です", case_id=start["case_id"])
        self.assertEqual(missing["case_summary"]["information_request"], "contact")
        result = self.flow.advance("企画調整グループです", case_id=start["case_id"])
        self.assertEqual(result["action"], "answer_contact")
        self.assertEqual(result["case_summary"]["purpose"], "meeting")
        self.assertEqual(result["contact"]["extension"], "8102")
        self.assertNotIn("head_motion", result)
        self.assertNotIn("総合受付", result["message"])

    def test_contact_requirements_can_arrive_in_different_orders(self):
        for turns in (("企画調整グループです", "森川さんです"),
                      ("森川さんです", "企画調整グループです"),
                      ("会議です", "企画調整グループです", "森川さんです"),
                      ("企画調整グループです", "会議です", "森川さんです")):
            with self.subTest(turns=turns):
                start = self.flow.advance("内線を教えてください")
                for text in turns:
                    result = self.flow.advance(text, case_id=start["case_id"])
                self.assertEqual(result["action"], "answer_contact")
                self.assertEqual(result["contact"]["extension"], "8102")
                self.assertNotIn("head_motion", result)

    def test_name_purpose_and_contact_question_can_be_in_one_turn(self):
        r = self.flow.advance("企画調整グループの森川将大さんと会議です。内線を教えてください")
        self.assertEqual(r["action"], "answer_contact")
        self.assertEqual(r["contact"]["extension_reading"], "はち・いち・ぜろ・に")
        self.assertEqual(r["case_summary"]["purpose"], "meeting")

    def test_contact_query_after_meeting_guidance_is_answered(self):
        start = self.flow.advance("企画調整グループの森川将大さんと会議です")
        self.assertEqual(start["action"], "guide")
        r = self.flow.advance("で、内線は？", case_id=start["case_id"])
        self.assertEqual(r["action"], "repeat_contact")
        self.assertEqual(r["contact"]["extension"], "8102")
        self.assertNotIn("head_motion", r)

    def test_building_query_after_meeting_guidance_is_not_done(self):
        start = self.flow.advance("企画調整グループの森川将大さんと会議です")
        r = self.flow.advance("研修塔はどこですか", case_id=start["case_id"])
        self.assertEqual(r["action"], "guide")
        self.assertEqual(r["message"], "研修棟は建物を出て右側にお進みください。")
        self.assertFalse(r["entry_permission_confirmed"])

    def test_new_contact_question_never_inherits_another_case(self):
        self.flow.advance("企画調整グループの森川将大さんと会議です")
        r = self.flow.advance("さっきの人の内線は？")
        self.assertEqual(r["action"], "ask_name")
        self.assertEqual(r["case_summary"]["staff_name"], "")
        self.assertNotIn("contact", r)

    def test_logged_spelling_mismatch_is_retained_and_confirmed_after_group(self):
        start = self.flow.advance("内線電話番号わかりますか？")
        mismatch = self.flow.advance("森川正宏さんと会議です。", case_id=start["case_id"], staff_name="森川正宏")
        self.assertEqual(mismatch["action"], "ask_group")
        self.assertEqual(mismatch["case_summary"]["reported_name"], "森川正宏")
        pending = self.flow.advance("企画調整グループです", case_id=start["case_id"])
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertIn("森川将大", pending["message"])
        self.assertIn("内線", pending["message"])
        self.assertNotIn("contact", pending)
        r = self.flow.advance("はい、合ってます", case_id=start["case_id"],
                              confirmation_token=pending["confirmation_token"])
        self.assertEqual(r["action"], "answer_contact")
        self.assertEqual(r["contact"]["extension"], "8102")

    def test_camera_contact_query_requires_current_confirmation(self):
        start = self.flow.advance("内線を教えてください")
        pending = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川将大")
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertNotIn("contact", pending)
        no_token = self.flow.advance("はい", case_id=start["case_id"])
        self.assertEqual(no_token["action"], "confirm_recipient")
        r = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=pending["confirmation_token"])
        self.assertEqual(r["contact"]["extension"], "8102")

    def test_contact_query_does_not_force_document_reading_into_read_only(self):
        start = self.flow.advance("内線を教えてください")
        r = self.flow.advance("この宛名を読んでください", case_id=start["case_id"])
        self.assertEqual(r["action"], "prepare_camera")
        self.assertEqual(r["case_summary"]["information_request"], "contact")

    def test_unknown_registered_extension_does_not_invent_number(self):
        for lookup in (self.flow.contact_lookup, None):
            with self.subTest(legacy_resolver=lookup is None):
                self.flow.contact_lookup = lookup
                r = self.flow.advance("田中さんの内線を教えてください")
                self.assertEqual(r["action"], "handoff")
                self.assertNotEqual(r["action"], "answer_contact")
                self.assertNotIn("contact", r)
                self.assertIn("登録", r["message"])

    def test_negative_contact_request_does_not_return_contact(self):
        r = self.flow.advance("内線ではなく、企画調整グループの森川将大さんと会議です")
        self.assertEqual(r["action"], "guide")
        self.assertNotIn("contact", r)

    def test_polite_contact_refusal_never_returns_number(self):
        for ending in ("内線はいりません", "内線は教えないでください", "電話番号を調べなくていいです"):
            with self.subTest(ending=ending):
                r = self.flow.advance("企画調整グループの森川将大さんの" + ending)
                self.assertNotIn("contact", r)
                self.assertNotEqual(r["action"], "answer_contact")

    def test_thanks_after_contact_answer_never_starts_another_reception(self):
        for purpose in ("", "会議です。"):
            with self.subTest(purpose=purpose):
                start = self.flow.advance("企画調整グループの森川将大さんの内線を教えて。" + purpose)
                self.assertEqual(start["action"], "answer_contact")
                r = self.flow.advance("ありがとう", case_id=start["case_id"])
                self.assertEqual(r["action"], "done")
                self.assertNotIn("head_motion", r)

    def test_where_followup_repeats_guidance_without_body_motion(self):
        start = self.flow.advance("企画調整グループの森川将大さんと会議です")
        r = self.flow.advance("どこ行けばいいですか？", case_id=start["case_id"])
        self.assertEqual(r["action"], "repeat_guidance")
        self.assertEqual(r["message"], start["message"])
        self.assertNotIn("head_motion", r)

    def test_where_followup_after_building_answer_uses_latest_place(self):
        start = self.flow.advance("企画調整グループの森川将大さんと会議です")
        building = self.flow.advance("研修棟はどこ？", case_id=start["case_id"])
        r = self.flow.advance("どこですか？", case_id=start["case_id"])
        self.assertEqual(r["message"], building["message"])
        self.assertEqual(r["action"], "repeat_guidance")
        self.assertNotIn("head_motion", r)

    def test_where_without_previous_guidance_asks_destination(self):
        r = self.flow.advance("どこ？")
        self.assertEqual(r["action"], "ask_destination")
        self.assertNotIn("head_motion", r)

    def test_unknown_followup_question_is_not_silently_discarded(self):
        start = self.flow.advance("企画調整グループの森川将大さんと会議です")
        r = self.flow.advance("それはどういうことですか？", case_id=start["case_id"])
        self.assertEqual(r["action"], "ask_followup")
        self.assertTrue(r["message"])

    def test_building_question_never_discards_raw_name_correction(self):
        start = self.flow.advance("企画調整グループの森川将大さんと会議です")
        changed = self.flow.advance("研修塔はどこですか。相手は森川健一さんです", case_id=start["case_id"])
        self.assertEqual(changed["case_summary"]["staff_name"], "森川健一")
        self.assertFalse(changed["case_summary"]["recipient_confirmed"])
        r = self.flow.advance("内線を教えて", case_id=start["case_id"])
        self.assertNotEqual(r.get("contact", {}).get("extension"), "8102")

    def test_raw_unmatched_full_name_is_kept_without_structured_name(self):
        start = self.flow.advance("内線を教えて")
        mismatch = self.flow.advance("森川正宏さんと会議です", case_id=start["case_id"])
        self.assertEqual(mismatch["case_summary"]["reported_name"], "森川正宏")
        self.assertEqual(mismatch["action"], "ask_group")
        pending = self.flow.advance("企画調整グループです", case_id=start["case_id"])
        self.assertEqual(pending["action"], "confirm_recipient")
        self.assertNotIn("contact", pending)

    def test_revoked_meetup_is_not_replayed_by_where_followup(self):
        start = self.flow.advance("企画調整グループの森川将大さんに荷物です")
        self.flow.advance("研修棟に来てと言われました", case_id=start["case_id"])
        revoked = self.flow.advance("研修棟には行きません", case_id=start["case_id"])
        self.assertEqual(revoked.get("reason"), "meetup_unconfirmed")
        r = self.flow.advance("どこですか", case_id=start["case_id"])
        self.assertEqual(r["action"], "ask_destination")
        self.assertNotIn("受渡し場所へお進み", r["message"])
        self.assertNotIn("head_motion", r)

    def test_revoked_public_direction_is_not_replayed_by_where_followup(self):
        start = self.flow.advance("研修棟はどこですか")
        self.flow.advance("研修棟には行きません", case_id=start["case_id"])
        r = self.flow.advance("どこですか", case_id=start["case_id"])
        self.assertEqual(r["action"], "ask_destination")
        self.assertNotIn("右側", r["message"])

    def test_unmatched_full_name_reopens_confirmation_even_when_surname_unchanged(self):
        start = self.flow.advance("企画調整グループの森川さんと会議です")
        self.assertTrue(start["case_summary"]["recipient_confirmed"])
        r = self.flow.advance("森川正宏さんの内線を教えてください", case_id=start["case_id"])
        self.assertEqual(r["action"], "confirm_recipient")
        self.assertFalse(r["case_summary"]["recipient_confirmed"])
        self.assertNotIn("contact", r)

    def test_public_building_question_needs_destination_not_recipient_confirmation(self):
        start = self.flow.advance("配達です。この宛名を読んでください")
        pending = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川将大")
        r = self.flow.advance("研修棟はどこですか", case_id=start["case_id"])
        self.assertEqual(r["action"], "guide")
        self.assertFalse(r["case_summary"]["recipient_confirmed"])
        self.assertFalse(r["entry_permission_confirmed"])
        self.assertNotIn("contact", r)
        # Answering a public direction question does not authorize the pending recipient.
        no_token = self.flow.advance("内線を教えて", case_id=start["case_id"])
        self.assertEqual(no_token["action"], "confirm_recipient")
        self.assertEqual(no_token["confirmation_token"], pending["confirmation_token"])

    def test_changed_recipient_invalidates_contact_confirmation(self):
        start = self.flow.advance("内線を教えて")
        old = self.flow.advance("", case_id=start["case_id"], source="camera", staff_name="森川将大")
        self.assertEqual(old["action"], "confirm_recipient")
        changed = self.flow.advance("森川健一さんです", case_id=start["case_id"], source="voice_correction")
        stale = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=old["confirmation_token"])
        self.assertNotIn("contact", stale)
        r = self.flow.advance("はい", case_id=start["case_id"], confirmation_token=changed["confirmation_token"])
        self.assertEqual(r["contact"]["extension"], "8202")


class FlowLifecycleTests(unittest.TestCase):
    def make_flow(self, **kwargs):
        from reception_flow import ReceptionFlow
        return ReceptionFlow(app.load_data, app._document_recipient_guidance,
                             app.staff_candidates, app.matched_group_ids,
                             app.evidence_staff_query, **kwargs)

    def test_expired_case_restarts_instead_of_inheriting_identity(self):
        now = [0.0]
        flow = self.make_flow(clock=lambda: now[0], ttl_seconds=30)
        start = flow.advance("田中さんに荷物です")
        now[0] = 30.0
        result = flow.advance("はい", case_id=start["case_id"])
        self.assertEqual(result["reason"], "case_not_found")
        self.assertNotIn("head_motion", result)

    def test_capacity_evicts_old_case_without_crossing_identity(self):
        flow = self.make_flow(max_cases=1)
        old = flow.advance("田中さんに荷物です")
        new = flow.advance("会議です")
        self.assertEqual(new["action"], "ask_name")
        result = flow.advance("はい", case_id=old["case_id"])
        self.assertEqual(result["reason"], "case_not_found")

    def test_retake_does_not_fill_missing_group_from_old_ocr(self):
        flow = self.make_flow()
        start = flow.advance("荷物です")
        flow.advance("", case_id=start["case_id"], source="camera",
                     staff_name="斉藤一郎", group_name="企画調整グループ")
        result = flow.advance("", case_id=start["case_id"], source="camera", staff_name="斉藤一郎")
        self.assertEqual(result["action"], "ask_group")
        self.assertEqual(result["case_summary"]["group_name"], "")


if __name__ == "__main__":
    unittest.main()
