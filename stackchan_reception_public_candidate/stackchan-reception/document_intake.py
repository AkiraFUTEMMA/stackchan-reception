"""AI/VLMの文書種別推定と、来訪者の用件の確定を分離する軽量ゲート。

文書種別 document_type は画像理解AIによる推定であって、用件の証拠にはならない。
用件 user_utterance は来訪者の実際の発話をそのまま使う。モデルの内心やCoTは不要。
このツールは宛先確定・MCP受付先照合・首振りを一切行わない。
"""
from __future__ import annotations

import unicodedata
from reception_flow import without_denied_purposes

VALID_TYPES = ("parcel_label", "training_notice", "business_card", "other", "unreadable")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").replace(" ", "").replace("　", "").lower()


def _user_purpose(utterance: str) -> str:
    """ユーザー発話に書かれた目的だけを保守的に検出。文書内容は参照しない。"""
    text = _norm(without_denied_purposes(utterance))
    # 同じ発話に異なる目的が含まれたとき、LLMに勝手に選ばせない。
    purposes = []
    if any(x in text for x in (
        "届けたい", "届けに来", "届けます", "配達", "納品", "預けたい", "所内便を", "荷物を届け", "荷物を預け",
    )):
        purposes.append("delivery")
    if any(x in text for x in (
        "研修に参加", "研修の参加", "研修を受講", "研修の受付", "研修に来", "受講しに", "講習に参加",
        "この研修参加", "この研修に参加",
    )):
        purposes.append("training")
    if any(x in text for x in (
        "会いに来", "面会", "訪ねたい", "訪問したい", "打ち合わせ", "打合せ", "会議に来", "お約束",
    )):
        purposes.append("meeting")
    if len(purposes) > 1:
        return "ambiguous"
    if purposes:
        return purposes[0]
    # 「読んで」は用件を確定する語ではない。資料の説明を頼んでいるだけ。
    if any(x in text for x in ("読んで", "読み取", "文字を教え", "何て書", "なんて書", "内容を教えて")):
        return "read_only"
    return "unknown"


def document_intake_guidance(document_type: str, user_utterance: str) -> dict:
    """文書種別×本人発話から、次の行動の種類だけを決める。案内先は決めない。"""
    kind = _norm(document_type)
    if kind not in VALID_TYPES:
        return {
            "success": False, "document_type": "unreadable", "purpose": "unknown",
            "needs_clarification": True, "may_route": False, "can_point": False,
            "next_tool": "", "reason": "unsupported_document_type",
            "message": "書類の種類を確認できません。どのようなご用件でしょうか？",
        }

    declared = _user_purpose(user_utterance)
    base = {"success": True, "document_type": kind, "purpose": declared,
            "may_route": False, "can_point": False, "next_tool": ""}
    if kind == "unreadable":
        return {**base, "needs_clarification": True, "reason": "document_unreadable",
                "message": "書類を判別できません。見出しを大きく映して撮り直すか、ご用件を口頭で教えてください。"}
    if kind == "other":
        return {**base, "needs_clarification": True, "reason": "unsupported_document",
                "message": "この書類だけでは案内方法が決まりません。何をお調べしましょうか？"}
    if declared == "ambiguous":
        return {**base, "needs_clarification": True, "reason": "ambiguous_purpose",
                "message": "ご用件を一つ確認させてください。お届け、研修、面会のどちらでしょうか？"}
    if declared == "read_only":
        return {**base, "needs_clarification": False, "reason": "read_only",
                "message": "読み取れた文字を簡潔に伝えてください。受付案内は依頼されるまで行いません。"}

    mapping = {
        "parcel_label": ("delivery", "get_label_delivery_guidance", "お荷物のお届けでしょうか？"),
        "training_notice": ("training", "get_training_guidance", "こちらの研修へのご参加でしょうか？"),
        "business_card": ("meeting", "get_reception_guidance", "こちらの名刺の方を訪ねるご用件でしょうか？"),
    }
    purpose, next_tool, prompt = mapping[kind]
    if declared == "unknown":
        return {**base, "needs_clarification": True, "reason": "purpose_not_confirmed",
                "suggested_purpose": purpose, "message": prompt}
    if declared != purpose:
        return {**base, "needs_clarification": True, "reason": "document_purpose_mismatch",
                "message": "お話のご用件と書類の種類が異なるようです。どのような案内をご希望ですか？"}
    # このツールは「次に使うツール」を示すだけ。氏名、研修名は別途確認する。
    return {**base, "needs_clarification": False, "may_route": True,
            "reason": "purpose_explicit", "next_tool": next_tool,
            "message": "", "required_confirmation": (
                "氏名と所属を来訪者に復唱してから確定" if purpose == "delivery"
                else "研修名を施設DBと照合" if purpose == "training"
                else "名刺の相手を確認し、面会予約は推定しない")}
