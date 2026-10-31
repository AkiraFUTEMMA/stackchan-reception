"""来訪者視点の案内方向を、本体へ渡す角度に変換する。実機の実行は行わない。"""


def with_head_motion(result: dict) -> dict:
    """成功した最初の案内先だけに動作指示を付ける。正面はツール不要。"""
    if (result.get("success") is not True or result.get("needs_clarification")
            or result.get("can_point") is False):
        return result
    direction = result.get("direction")
    if direction not in ("left", "right", "front"):
        return result
    motion = {"coordinate_frame": "visitor", "point": None, "return_to_front": None}
    if direction != "front":
        motion["point"] = {"yaw": 45 if direction == "left" else -45,
                           "pitch": 0, "speed": 500}
        motion["return_to_front"] = {"yaw": 0, "pitch": 0, "speed": 500}
    return {**result, "head_motion": motion}
