"""文書分類と訪問目的の分離：ライブ認識とは独立した回帰試験。"""
import unittest
from document_intake import document_intake_guidance as guide

class IntakeTests(unittest.TestCase):
    def test_generic_read_label_never_implies_delivery(self):
        r=guide("parcel_label", "ちょっと読み取りお願いします")
        self.assertFalse(r["may_route"])
        self.assertEqual(r["reason"], "read_only")
    def test_generic_read_training_never_implies_participation(self):
        r=guide("training_notice", "ちょこれ読んでくれる？")
        self.assertFalse(r["may_route"])
        self.assertEqual(r["reason"], "read_only")
    def test_generic_please_parcel_requests_purpose(self):
        r=guide("parcel_label", "これお願いできますか")
        self.assertTrue(r["needs_clarification"])
        self.assertIn("お届け",r["message"])
    def test_generic_please_training_requests_purpose(self):
        r=guide("training_notice", "これお願いします")
        self.assertEqual(r["reason"],"purpose_not_confirmed")
        self.assertIn("研修",r["message"])
    def test_generic_please_business_card_requests_purpose(self):
        r=guide("business_card", "これお願いします")
        self.assertIn("訪ねる",r["message"])
        self.assertFalse(r["may_route"])
    def test_explicit_parcel(self):
        r=guide("parcel_label", "斎藤さんに荷物を届けたい")
        self.assertTrue(r["may_route"])
        self.assertEqual(r["next_tool"],"get_label_delivery_guidance")
        self.assertFalse(r["can_point"])
    def test_explicit_training(self):
        r=guide("training_notice", "この研修に参加するので受付を教えて")
        self.assertTrue(r["may_route"])
        self.assertEqual(r["next_tool"],"get_training_guidance")
    def test_explicit_meeting_business_card(self):
        r=guide("business_card", "この名刺の方を訪ねたい")
        self.assertTrue(r["may_route"])
        self.assertEqual(r["next_tool"],"get_reception_guidance")
    def test_mismatch_no_route(self):
        r=guide("parcel_label", "この研修に参加したい")
        self.assertEqual(r["reason"], "document_purpose_mismatch")
        self.assertFalse(r["may_route"])
    def test_unknown_doc_fails_safe(self):
        r=guide("unreadable", "これお願いできますか")
        self.assertFalse(r["may_route"])
        self.assertTrue(r["needs_clarification"])
    def test_unsupported_doc_fails_safe(self):
        r=guide("receipt", "これを届けます")
        self.assertFalse(r["success"])
        self.assertFalse(r["may_route"])
    def test_tool_registry(self):
        from test_facility_registration import DummyMCP
        from test_reception import app
        from facility_tools import register_facility_tools
        m=DummyMCP()
        handlers=register_facility_tools(m,app.load_data)
        self.assertNotIn("get_document_intake_guidance", m.tools)
        self.assertFalse(handlers["get_document_intake_guidance"]("parcel_label", "これ読んで")["may_route"])
    def test_ambiguous_purpose_no_route(self):
        r=guide("parcel_label", "荷物を届けたいし、研修に参加したい")
        self.assertEqual(r["reason"], "ambiguous_purpose")
    def test_unknown_and_other(self):
        r=guide("other","これお願い")
        self.assertFalse(r["may_route"])
        self.assertEqual(r["reason"],"unsupported_document")
if __name__=="__main__": unittest.main()
