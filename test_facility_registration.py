"""Registration smoke test without installing fastmcp (stdlib)."""
import unittest
from facility_tools import register_facility_tools

class DummyMCP:
    def __init__(self): self.tools={}
    def tool(self):
        def decorate(fn): self.tools[fn.__name__]=fn; return fn
        return decorate

class RegistrationSmoke(unittest.TestCase):
    def setUp(self):
        # test_reception injects a stdlib-only FastMCP stub if needed.
        from test_reception import app
        self.mcp=DummyMCP()
        self.handlers=register_facility_tools(self.mcp,app.load_data)
    def test_registration_only_three_public_facility_tools(self):
        self.assertEqual(set(self.mcp.tools),{"get_place_guidance","get_training_guidance","get_facility_policy"})
    def test_workplace_mismatch_not_guided(self):
        r=self.handlers["get_staff_workplace"]("斎藤一郎")
        self.assertFalse(r["success"])
        self.assertFalse(r["can_point"])
    def test_training_first_checkin(self):
        r=self.mcp.tools["get_training_guidance"]("環境モニタリング基礎研修")
        self.assertEqual(r["first_destination"]["place_id"],"L_ENTRANCE")
    def test_staff_direction_front(self):
        r=self.handlers["get_staff_workplace"]("斎藤一郎","計測技術グループ")
        self.assertEqual(r["direction"],"front")
    def test_policy_smoking(self):
        r=self.mcp.tools["get_facility_policy"]("喫煙")
        self.assertEqual(r["direction"],"right")
    def test_training_building_and_spoken_homophone_use_registered_wayfinding(self):
        for name in ("研修棟", "研修塔"):
            with self.subTest(name=name):
                r=self.mcp.tools["get_place_guidance"](name)
                self.assertTrue(r["success"])
                self.assertEqual(r["building_id"], "B02")
                self.assertEqual(r["building_name"], "研修棟")
                self.assertEqual(r["message"], "研修棟は建物を出て右側にお進みください。")
                self.assertEqual(r["head_motion"]["point"]["yaw"], -45)
                self.assertNotIn("受付", r["message"])
                self.assertFalse(r["entry_permission_confirmed"])
    def test_building_fallback_keeps_unknown_name_unresolved(self):
        r=self.mcp.tools["get_place_guidance"]("未登録の塔")
        self.assertFalse(r["success"])
        self.assertNotIn("head_motion", r)
    def test_building_fallback_keeps_private_room_checkin(self):
        r=self.mcp.tools["get_place_guidance"]("企画調整室")
        self.assertTrue(r["must_checkin"])
        self.assertEqual(r["direction"], "front")
if __name__=="__main__": unittest.main()
