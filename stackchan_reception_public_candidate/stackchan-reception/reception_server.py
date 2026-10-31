"""StackChan受付MCP。架空データを使った照合と案内を公開する。"""

import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from guidance_motion import with_head_motion
from reception_flow import ReceptionFlow, without_denied_purposes

mcp = FastMCP("StackChanReception")
DATA_FILE = Path(__file__).resolve().parent / "reception_data.json"


def log(text: str) -> None:
    print(f"[MCP] {text}", file=sys.stderr, flush=True)


def normalized(text: str) -> str:
    """名前・読み・所属を比較するための軽い正規化。"""
    text = unicodedata.normalize("NFKC", text or "").strip()
    for symbol in (" ", "　", "、", "。", "?", "？", "・", "\n", "\t"):
        text = text.replace(symbol, "")
    for suffix in ("さん", "さま", "様", "氏"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def load_data() -> dict:
    """変更されたJSONを毎回読み込み、参照関係を検査する。"""
    with DATA_FILE.open(encoding="utf-8-sig") as f:
        data = json.load(f)
    validate_data(data)
    return data


def validate_data(data: dict) -> None:
    """誤った参照先に黙ってフォールバックしないための検証。"""
    orgs = data["organizations"]
    groups = data["groups"]
    buildings = data["buildings"]
    routes = data["reception"]
    for key, route in routes.items():
        if route["direction"] not in ("left", "right", "front"):
            raise ValueError(f"reception.{key}: invalid direction")
        if not route.get("message") or not route.get("destination"):
            raise ValueError(f"reception.{key}: incomplete destination/message")
        if route.get("contact_method") not in (None, "extension"):
            raise ValueError(f"reception.{key}: invalid contact_method")
    for key, group in groups.items():
        if group["organization_id"] not in orgs:
            raise ValueError(f"groups.{key}: unknown organization")
    for key, building in buildings.items():
        if building["organization_id"] not in orgs:
            raise ValueError(f"buildings.{key}: unknown organization")
        if building["reception_route"] not in routes:
            raise ValueError(f"buildings.{key}: unknown reception route")
    for staff_id, staff in data["staff"].items():
        for group_id in staff["group_ids"]:
            if group_id not in groups:
                raise ValueError(f"staff.{staff_id}: unknown group {group_id}")
        for route in staff.get("routes", {}).values():
            if route not in routes:
                raise ValueError(f"staff.{staff_id}: unknown route {route}")


def validate_request_type(request_type: str, utterance: str) -> str:
    """発話に明示されない来訪目的はunknownにする（保守的な簡易検証）。"""
    text = normalized(without_denied_purposes(utterance))
    keywords = {
        "delivery": ("荷物", "配達", "宅配", "届け", "納品", "お預け"),
        "meeting": ("会議", "打ち合わせ", "打合せ", "面談", "面会", "約束", "アポイント"),
        "other_building": ("研修棟", "研修", "別棟", "別の建物"),
    }
    matches = [key for key, words in keywords.items() if any(w in text for w in words)]
    return request_type if len(matches) == 1 and matches[0] == request_type else "unknown"


def full_name(staff: dict) -> str:
    return staff["family_name"] + staff["given_name"]


def staff_candidates(data: dict, name: str) -> list[tuple[str, dict]]:
    """苗字・氏名・読みで検索。同音異字は同じ候補群として返す。"""
    q = normalized(name)
    if not q:
        return []
    raw = []
    for sid, person in data["staff"].items():
        keys = (
            full_name(person), person["family_name"],
            person["reading"], person["family_reading"],
            person["family_name"] + person["reading"][len(person["family_reading"]):],
            *person.get("family_aliases", []),
            *person.get("name_aliases", []),
            *(alias + person["given_name"] for alias in person.get("family_aliases", [])),
        )
        if q in {normalized(k) for k in keys}:
            raw.append((sid, person))
    if not raw:
        return []
    # 「斎藤一郎」「齋藤一郎」の読みが同一なら、漢字の認識だけで決めつけない。
    matched_readings = {p["reading"] for _, p in raw if q in {
        normalized(full_name(p)), normalized(p["reading"]),
        normalized(p["family_name"] + p["reading"][len(p["family_reading"]):]),
        *(normalized(alias) for alias in p.get("name_aliases", []))}}
    matched_family_readings = {p["family_reading"] for _, p in raw if q == normalized(p["family_name"]) or q == normalized(p["family_reading"])}
    result = []
    for sid, person in data["staff"].items():
        if (
            (sid, person) in raw
            or person["reading"] in matched_readings
            or (person["family_reading"] in matched_family_readings)
        ):
            result.append((sid, person))
    return result


def group_names(data: dict, person: dict) -> list[str]:
    names = [data["groups"][gid]["name"] for gid in person["group_ids"]]
    return names if names else [person.get("role", "センター付")]


def candidate_view(data: dict, sid: str, person: dict) -> dict:
    """来訪者に提示してよい最小限の項目のみを返す。"""
    return {"staff_id": sid, "name": full_name(person), "groups": group_names(data, person)}


def ambiguity_message(data: dict, candidates: list[tuple[str, dict]]) -> str:
    """候補構成に応じて、区別に役立つ質問を一つだけ返す。"""
    views = [candidate_view(data, sid, p) for sid, p in candidates]
    # 同音同名の場合、下の名前を聞いても識別できない。
    same_reading = len({p.get("reading", "") for _, p in candidates}) == 1
    distinct_groups = [tuple(group_names(data, p)) for _, p in candidates]
    if same_reading and len(set(distinct_groups)) == len(distinct_groups):
        group_labels = ["・".join(groups) for groups in distinct_groups]
        if len(group_labels) == 2:
            return f"同じ読みのお名前の方が2名います。所属は「{group_labels[0]}」と「{group_labels[1]}」のどちらでしょうか？"
        return "同じ読みのお名前の方が複数います。所属グループを教えてください。"
    if same_reading:
        return "同じ読みのお名前の方が複数います。所属など、区別できる情報を教えてください。"
    return "同じ名字の方が複数います。所属グループ、または下のお名前を教えてください。"


def matched_group_ids(data: dict, utterance: str) -> set[str]:
    """発話中に明示されたグループだけを確認条件に使う。"""
    text = normalized(utterance)
    found = set()
    for gid, group in data["groups"].items():
        aliases = [group["name"], *group.get("aliases", [])]
        if any(normalized(alias) in text for alias in aliases):
            found.add(gid)
    if "センター付" in text:
        found.add("__CENTER__")
    return found


def is_bare_confirmation(utterance: str) -> bool:
    """根拠を追加しない肯定応答では、同音・同姓の候補を確定しない。"""
    phrase = normalized(utterance)
    return phrase in {
        "はい", "はいそうです", "そうです", "そうですね", "ええ", "ええそうです",
        "その方です", "その人です", "間違いないです", "間違いありません", "合っています",
    }


def evidence_staff_query(data: dict, utterance: str) -> str:
    """AIがstaff_nameを省略したとき、発話から明示された名字を補う。"""
    text = normalized(utterance)
    def explicit_full_name(alias: str) -> bool:
        # Do not truncate an unknown longer name to a registered name.
        # Accept only an end, honorific, or a conservative visit-related suffix.
        suffix = (r"(?=$|(?:さん|さま|様|氏)(?:$|[へにとのはがをもでか])|"
                  r"です|でした|宛|への|に荷物|に配達|に届け|に会|に面会|との会議|の内線|の荷物|の配達|の書類)")
        return re.search(re.escape(normalized(alias)) + suffix, text) is not None
    full_hits = [
        alias
        for p in data["staff"].values()
        for alias in [full_name(p), p["reading"],
                      p["family_name"] + p["reading"][len(p["family_reading"]):],
                      *(fam + p["given_name"] for fam in p.get("family_aliases", [])),
                      *p.get("name_aliases", [])]
        if explicit_full_name(alias)
    ]
    if full_hits:
        return sorted(full_hits, key=len, reverse=True)[0]
    family_hits = [
        alias
        for p in data["staff"].values()
        for alias in [p["family_name"], p["family_reading"], *p.get("family_aliases", [])]
        if normalized(alias) in text
    ]
    unique = list(dict.fromkeys(family_hits))
    # 異なる名字が複数登場した場合は勝手に選ばない。
    return unique[0] if len(unique) == 1 else ""


def external_building(data: dict, utterance: str) -> dict | None:
    text = normalized(utterance)
    for building in data["buildings"].values():
        if data["organizations"][building["organization_id"]]["managed_by_us"]:
            continue
        aliases = [building["name"], *building.get("aliases", [])]
        if any(normalized(alias) in text for alias in aliases):
            return building
    return None


def _staff_contact(data: dict, staff_id: str) -> dict | None:
    person = data["staff"].get(staff_id, {})
    extension = person.get("demo_extension")
    if not isinstance(extension, str) or not extension.isascii() or not extension.isdigit() or not 3 <= len(extension) <= 6:
        return None
    return {"staff_id": staff_id, "staff_name": full_name(person), "reading": person["reading"],
            "extension": extension, "extension_reading": "・".join(
                ("ぜろ", "いち", "に", "さん", "よん", "ご", "ろく", "なな", "はち", "きゅう")[int(d)] for d in extension),
            "source": "fictional_directory", "automatic_dial": False, "simulation_only": True}


def _contact_guidance(data: dict, staff_name: str, group_name: str) -> dict:
    gids = matched_group_ids(data, group_name)
    matches = [(sid, person) for sid, person in staff_candidates(data, staff_name)
               if set(person["group_ids"]) & gids or (not person["group_ids"] and "__CENTER__" in gids)]
    if len(matches) != 1:
        return {"success": False, "reason": "contact_recipient_unresolved",
                "message": "内線を調べる相手を特定できません。氏名と所属を確認してください。"}
    contact = _staff_contact(data, matches[0][0])
    if contact is None:
        return {"success": False, "reason": "extension_unregistered",
                "message": "登録された内線番号を確認できません。総合受付でご確認ください。"}
    return {"success": True, "contact": contact,
            "message": f"{contact['staff_name']}様の内線は、{contact['extension_reading']}、です。"}


def route_result(data: dict, route_key: str, *, request_type: str, staff_id: str = "", staff_name: str = "") -> dict:
    """案内先の欠落を黙って見逃さない。"""
    if route_key not in data["reception"]:
        log(f"ERROR: route not found: {route_key}")
        return {
            "success": False, "reason": "route_not_found",
            "message": "案内情報を確認できませんでした。正面の総合受付へお尋ねください。",
            "direction": "front",
        }
    route = data["reception"][route_key]
    contact = None
    message = route["message"]
    if route.get("contact_method") == "extension":
        contact = _staff_contact(data, staff_id)
        if contact is None:
            return {"success": False, "reason": "extension_unregistered",
                    "message": "登録された内線番号を確認できません。総合受付でご確認ください。"}
        message += f"{contact['staff_name']}様の内線は、{contact['extension_reading']}、です。お電話で受渡し方法をご確認ください。"
    log(f"route: staff_id={staff_id or '-'}, purpose={request_type}, route={route_key}")
    return with_head_motion({
        "success": True,
        "request_type": request_type,
        "staff_id": staff_id,
        "staff_name": staff_name,
        "destination": route["destination"],
        "direction": route["direction"],
        "message": message,
        **({"contact": contact} if contact else {}),
    })


def search_staff_candidates(name: str, group_hint: str = "") -> dict:
    """架空の職員名簿から、同姓・同音異字も含めて候補を探す。候補が複数なら利用者に確認すること。

    name: 来訪者が発話した職員の名字・フルネーム・読み。
    group_hint: 来訪者が明示したグループ名。空文字列も可。
    このツールは氏名や所属の候補だけを返し、所在・内線・在席状況を返さない。
    """
    data = load_data()
    candidates = staff_candidates(data, name)
    if group_hint:
        gids = matched_group_ids(data, group_hint)
        if gids:
            candidates = [
                (sid, p) for sid, p in candidates
                if set(p["group_ids"]) & gids or (not p["group_ids"] and "__CENTER__" in gids)
            ]
    views = [candidate_view(data, sid, p) for sid, p in candidates]
    log(f"search_staff: name={name}, group={group_hint}, candidates={[v['staff_id'] for v in views]}")
    return {
        "success": bool(views),
        "matched_count": len(views),
        "needs_clarification": len(views) > 1,
        "candidates": views,
        "message": (ambiguity_message(data, candidates) if len(views) > 1
                    else "該当職員の候補が見つかりました。" if views
                    else "該当職員を確認できませんでした。総合受付にお尋ねください。"),
    }


def get_facility_guidance(place_name: str) -> dict:
    """架空の建物・別組織の受付案内を取得する。

    place_name: 来訪者が指定した建物名・施設名（例：研修棟、地域防災連絡所、オフサイトセンター）。
    別組織の職員名・在席・予定は検索しない。該当施設の受付のみ案内する。
    """
    data = load_data()
    query = normalized(place_name)
    for building in data["buildings"].values():
        aliases = [building["name"], *building.get("aliases", [])]
        if any(normalized(x) == query for x in aliases):
            org = data["organizations"][building["organization_id"]]
            result = route_result(data, building["reception_route"], request_type="facility")
            result.update({"facility": building["name"], "managed_by_us": org["managed_by_us"]})
            return result
    return {
        "success": False, "reason": "facility_not_found", "direction": "front",
        "message": "該当する施設の案内情報を確認できませんでした。正面の総合受付へお尋ねください。",
    }


def _mentioned_buildings(data: dict, utterance: str) -> list[dict]:
    query = normalized(utterance)
    buildings = []
    for building in data["buildings"].values():
        aliases = [building["name"], *building.get("aliases", [])]
        if any(re.search(r"(?:^|[でへのにをはとが]|って)" + re.escape(normalized(alias))
                         + r"(?=$|に|へ|で|は|を|と|前|入口|正面|です|どこ|って(?:ところ|所)(?:に|へ|で)|の(?:入口|正面|前|方|ほう|受付|場所|建物))",
                         query) for alias in aliases):
            buildings.append(building)
    return buildings


def _delivery_meetup_guidance(data: dict, utterance: str, known_facility: str = "",
                             known_staff_name: str = "") -> dict | None:
    """受渡し場所の申告と登録済みの道案内だけをつなぐ。通話・入館は確認しない。"""
    query = normalized(utterance)
    buildings = _mentioned_buildings(data, utterance)
    changed_place = (known_facility and any(word in query for word in ("変更", "変わりました", "別の場所"))
                     and any(word in query for word in ("場所", "行き先", "待合", "待ち合", "受渡", "受け渡")))
    if changed_place or (buildings or known_facility) and any(word in query for word in (
            "言われていません", "言われてない", "言われなかった", "来ないで", "来なくて",
            "行かない", "行きません", "受け取らない", "受け取れない", "かどうか", "ではなく", "じゃなく")):
        return {"action": "ask_correction", "reason": "meetup_unconfirmed", "source": "visitor_report",
                "message": "移動先を確認できません。相手の方から指定された受渡し場所を教えてください。"}
    asks_procedure = "手続" in query or ("受付" in query and any(word in query for word in ("必要", "やる", "済", "いります", "要る")))
    same_site = not buildings or (len(buildings) == 1 and buildings[0]["name"] == known_facility)
    if known_facility and same_site and asks_procedure:
        return {"action": "explain_delivery", "facility": known_facility,
                "guidance_context": "delivery_meetup", "source": "visitor_report",
                "message": "こちらでは宛先の照合と内線の案内まで行いました。受渡しは相手の方と決めた場所でお願いします。入館手続きの要否は確認できていません。"}
    if not buildings and known_facility and known_staff_name:
        people = staff_candidates(data, known_staff_name)
        if len(people) == 1:
            person = people[0][1]
            aliases = (person["family_name"], person["family_reading"], full_name(person), person["reading"])
            same_recipient = any(re.fullmatch(
                r"(?:電話で|えっと|あの)?" + re.escape(normalized(alias))
                + r"(?:さん|様)のところに(?:直接)?(?:来い|来て)(?:くれ)?(?:って|と)言われ(?:た|ました)"
                + r"(?:んですけど|のですが|けど|が|んですが|んです|です)?", query) for alias in aliases)
            if same_recipient:
                buildings = [building for building in data["buildings"].values() if building["name"] == known_facility]
    if len(buildings) != 1:
        return None
    building = buildings[0]
    if (not data["organizations"][building["organization_id"]]["managed_by_us"]
            or not building.get("wayfinding_message")):
        return None
    reported = any(word in query for word in ("言われた", "言われました", "言われて",
                                              "受け取るそう", "受け取ると言", "受け取るとの", "待ち合わせ", "待合せ"))
    if not reported and known_facility != building["name"]:
        return None
    result = route_result(data, building["reception_route"], request_type="facility")
    return {**result, "facility": building["name"], "destination": building["name"],
            "guidance_context": "delivery_meetup", "source": "visitor_report",
            "message": building["wayfinding_message"] + "お伝えいただいた受渡し場所へお進みください。"}


def _general_reception_guidance(data: dict, utterance: str, pending: dict) -> dict | None:
    """案件開始前の建物・通常勤務場所の照会も同じ入口で受ける。"""
    query = normalized(utterance)
    if any(word in query for word in ("行きません", "行かない", "行かなく", "来ないで", "来なくて")):
        return {"success": False, "action": "ask_correction",
                "message": "移動先を確認できません。行きたい建物を教えてください。"}
    gids = matched_group_ids(data, utterance)
    unknown = any(word in query for word in ("わかりません", "分かりません", "わからない", "分からない", "わかんな", "分かんな"))
    workplace = any(word in query for word in ("勤務場所", "通常の職場", "普段の職場", "居室"))
    continuing = pending.get("kind") == "workplace" and (gids or unknown)
    if workplace or continuing:
        if continuing and unknown and not gids:
            return {"success": False, "action": "handoff",
                    "message": "通常の勤務場所を特定できません。氏名と所属を総合受付でご確認ください。", "query_context": {}}
        name = evidence_staff_query(data, utterance) or pending.get("staff_name", "")
        group = (data["groups"][next(iter(gids))]["name"] if len(gids) == 1 and "__CENTER__" not in gids
                 else pending.get("group_name", ""))
        result = _facility_handlers["get_staff_workplace"](name, group)
        return {**result, "query_context": {"kind": "workplace", "staff_name": name, "group_name": group}}
    buildings = _mentioned_buildings(data, utterance)
    if len(buildings) == 1:
        result = _facility_handlers["get_place_guidance"](buildings[0]["name"])
        return result if result.get("success") else get_facility_guidance(buildings[0]["name"])
    return None


def get_reception_guidance(
    request_type: Literal["delivery", "meeting", "other_building", "unknown"],
    user_utterance: str,
    staff_name: str,
    staff_id: str = "",
    group_hint: str = "",
    prior_utterance: str = "",
) -> dict:
    """架空の「みらい技術センター」の受付先を調べる。来訪目的・人物の特定・案内先を推測しない。

    request_type:
      delivery: 荷物・配達・納品が発話に明示された場合。
      meeting: 会議・打ち合わせ・約束が発話に明示された場合。
      other_building: 研修棟など他の建物を訪問する場合。
      unknown: 「〇〇さんいます？」など来訪目的が不明な場合。勝手にmeeting扱いしない。
    user_utterance: 直近の来訪者の発話を言い換えずに渡す。
    staff_name: 職員の名字／フルネーム。名前がなければ空文字列（必ず指定）。
    staff_id: search_staff_candidatesで得た候補ID。未確認なら空文字列。IDだけで本人を決めつけない。
    group_hint: 来訪者が明示した所属グループ。なければ空文字列。
    prior_utterance: 確認質問を挟んだ場合、同じ会話で以前に来訪者が実際に話した用件文をそのまま指定。
      例：先に「山田さんに荷物です」、後で「企画調整グループの方です」と言ったときのみ使用。
      会話にない内容は作らないこと。

    同姓・同音異字等で一意に特定できなければ候補を返す。確認前に案内先を決めない。
    別組織は職員検索せず、その施設の受付を案内する。
    directionは来訪者から見た案内方向。成功した案内だけ方向へ首を動かす。
    """
    data = load_data()
    evidence = (prior_utterance + " " + user_utterance).strip()
    log(f"AI判定: {request_type}, staff={staff_name}, staff_id={staff_id}, utterance={user_utterance}, prior={prior_utterance}")

    # 外部組織の来訪は自組織の人名DBを検索しない。
    other = external_building(data, evidence)
    if other:
        org = data["organizations"][other["organization_id"]]
        if not org["managed_by_us"]:
            result = route_result(data, other["reception_route"], request_type="external_organization")
            result.update({"facility": other["name"], "managed_by_us": False})
            return result

    purpose = validate_request_type(request_type, evidence)
    log(f"検証結果: {purpose}")
    if purpose == "unknown":
        return {
            "success": False, "needs_clarification": True, "reason": "purpose_unknown",
            "message": "ご用件を確認させてください。お届け物でしょうか、お打ち合わせでしょうか、それとも別のご用件でしょうか？",
        }

    if purpose == "other_building":
        return route_result(data, "other_building", request_type=purpose)

    # 旧Day4の「田中さん宛て」「佐藤さん宛て」は、名前だけでも一意に特定可能。
    mentioned_name = evidence_staff_query(data, evidence)
    query_name = staff_name.strip() or mentioned_name
    if staff_name.strip() and mentioned_name:
        supplied_ids = {sid for sid, _ in staff_candidates(data, staff_name.strip())}
        mentioned_ids = {sid for sid, _ in staff_candidates(data, mentioned_name)}
        if supplied_ids and mentioned_ids and not supplied_ids.intersection(mentioned_ids):
            return {
                "success": False, "needs_clarification": True, "reason": "staff_name_mismatch",
                "message": "お名前の聞き取り結果が一致しません。お名前をもう一度確認させてください。",
            }
    if not query_name:
        # 宛名なしの配達や会議は、従来どおり共通の受付に誘導する。
        return route_result(data, purpose, request_type=purpose)

    candidates = staff_candidates(data, query_name)
    if not candidates:
        return {
            "success": False, "reason": "staff_not_found", "direction": "front",
            "message": f"{query_name}さんの受付情報を確認できませんでした。正面の総合受付でお尋ねください。",
        }

    # 身元の確定に用いる所属は、実際の発話に出現しているものだけ。
    explicitly_mentioned_groups = matched_group_ids(data, evidence)
    # group_hintは発話中の証拠と矛盾しないことを確認するために使う。
    hinted_groups = matched_group_ids(data, group_hint) if group_hint else set()
    if explicitly_mentioned_groups and hinted_groups and not explicitly_mentioned_groups.intersection(hinted_groups):
        return {
            "success": False, "needs_clarification": True, "reason": "group_hint_mismatch",
            "message": "所属グループの聞き取りが一致しません。もう一度教えてください。",
        }
    if explicitly_mentioned_groups:
        candidates = [
            (sid, person) for sid, person in candidates
            if set(person["group_ids"]) & explicitly_mentioned_groups
            or (not person["group_ids"] and "__CENTER__" in explicitly_mentioned_groups)
        ]
    if not candidates:
        return {
            "success": False, "needs_clarification": True, "reason": "group_mismatch",
            "message": "お名前と所属グループの組み合わせを確認できませんでした。もう一度お聞かせください。",
        }

    # 同姓でも下の名前まで発話されていれば絞り込む。ただし同音異字の人は残す。
    if len(candidates) > 1:
        spoken = normalized(evidence)
        mentioned = [
            (sid, p) for sid, p in candidates
            if normalized(full_name(p)) in spoken or normalized(p["reading"]) in spoken
        ]
        if len(mentioned) == 1:
            chosen = mentioned[0][1]
            if sum(p["reading"] == chosen["reading"] for _, p in candidates) == 1:
                candidates = mentioned

    # 追加の氏名・所属がない短い肯定だけでは、候補を決められない。
    # 同じ質問のループを防ぐため、再質問せず有人窓口へ引き継ぐ。
    if len(candidates) > 1 and is_bare_confirmation(user_utterance):
        log(f"unresolved confirmation: candidates={[sid for sid, _ in candidates]}")
        return {
            "success": False,
            "needs_clarification": False,
            "reason": "ambiguous_staff_unresolved",
            "message": (
                "はい、だけでは同じ読みのお名前の方を区別できませんでした。"
                "誤ったご案内を防ぐため、正面の総合受付で担当者をお確かめください。"
            ),
        }

    # staff_idは検索結果との整合だけ確認。曖昧な集合から任意の一人を選ぶ根拠にはしない。
    if len(candidates) > 1:
        views = [candidate_view(data, sid, p) for sid, p in candidates]
        log(f"ambiguous staff: {[v['staff_id'] for v in views]}")
        return {
            "success": False, "needs_clarification": True, "reason": "ambiguous_staff",
            "candidates": views,
            "message": ambiguity_message(data, candidates),
        }

    resolved_id, person = candidates[0]
    if staff_id and staff_id != resolved_id:
        return {
            "success": False, "needs_clarification": True, "reason": "staff_id_mismatch",
            "message": "お名前と職員の候補が一致しません。お名前をもう一度確認させてください。",
        }

    route_key = person.get("routes", {}).get(purpose, purpose)
    result = route_result(
        data, route_key, request_type=purpose,
        staff_id=resolved_id, staff_name=full_name(person),
    )
    return result



def _document_recipient_guidance(
    label_staff_name: str,
    label_group: str,
    visitor_confirmed: bool = False,
    source: Literal["camera", "voice_correction"] = "camera",
    request_type: str = "delivery",
) -> dict:
    """架空の配達宛名を来訪者に確認し、肯定後にだけ職員名簿で照合する。

    カメラ画像の読み取り結果は source="camera" で指定する。
    読み取り誤りを来訪者が口頭で訂正した場合は、source="voice_correction" とし、
    label_staff_name / label_group に来訪者が実際に言った氏名・所属を指定する。
    引数名は既存ツール互換のため label_* のまま維持する。
    必ず visitor_confirmed=False で復唱確認し、来訪者が明確に肯定した後に
    同じ氏名・所属と source を用いて visitor_confirmed=True で再呼び出しする。
    未確認の情報や読み取れない部分を推測・補完してはならない。

    success=False / needs_clarification=True の場合は message に沿って
    再撮影または口頭訂正を一度提案する。解決しない場合は総合受付へ引き継ぐ。
    """
    data = load_data()
    name = label_staff_name.strip()
    group = label_group.strip()
    log(f"label_delivery: name={name}, group={group}, confirmed={visitor_confirmed}, source={source}")

    if source not in ("camera", "voice_correction"):
        return {"success": False, "needs_clarification": True,
                "reason": "invalid_source", "message": "受付方法を確認できませんでした。撮影か口頭での訂正を選んでください。"}

    retry_choices = "再撮影するか、宛名の氏名と所属グループを口頭で教えてください。"
    if not name or not group:
        return {
            "success": False, "needs_clarification": True,
            "reason": "label_incomplete",
            "message": "宛名の氏名と所属グループを両方確認できませんでした。" + retry_choices,
            "next_actions": ["retake_photo", "voice_correction", "reception_desk"],
        }

    # 必ず復唱を先に行う。名簿に存在しない／所属が未知でもこの段階では却下しない。
    if not visitor_confirmed:
        lead = "写真から" if source == "camera" else "口頭で"
        return {
            "success": False, "needs_clarification": True,
            "reason": "label_needs_confirmation",
            "recognized_name": name, "recognized_group": group,
            "source": source,
            "message": f"{lead}『{group}の{name}さん』と確認しました。こちらで合っていますか？",
        }

    gids = matched_group_ids(data, group)
    # 肯定された名前と所属を、名簿に基づいて初めて照合する。
    if len(gids) != 1:
        return {
            "success": False, "needs_clarification": True,
            "reason": "label_group_unrecognized",
            "message": "お伝えいただいた所属を名簿で確認できませんでした。" + retry_choices,
            "next_actions": ["retake_photo", "voice_correction", "reception_desk"],
        }
    candidates = staff_candidates(data, name)
    if not candidates:
        return {
            "success": False, "needs_clarification": True,
            "reason": "label_staff_not_found",
            "message": "お名前が名簿に見当たりません。読み間違いの可能性もあります。" + retry_choices,
            "next_actions": ["retake_photo", "voice_correction", "reception_desk"],
        }
    matches = [
        (sid, person) for sid, person in candidates
        if set(person["group_ids"]) & gids
        or (not person["group_ids"] and "__CENTER__" in gids)
    ]
    if not matches:
        return {
            "success": False, "needs_clarification": True,
            "reason": "label_name_group_mismatch",
            "message": "氏名と所属の組み合わせが名簿に見当たりません。" + retry_choices,
            "next_actions": ["retake_photo", "voice_correction", "reception_desk"],
        }
    if len(matches) > 1:
        return {
            "success": False, "needs_clarification": True,
            "reason": "label_staff_ambiguous",
            "message": "同じ宛名に複数の職員が該当します。" + retry_choices,
            "next_actions": ["retake_photo", "voice_correction", "reception_desk"],
        }
    sid, person = matches[0]
    route_key = person.get("routes", {}).get(request_type, request_type)
    return route_result(data, route_key, request_type=request_type, staff_id=sid,
                        staff_name=full_name(person))


def get_label_delivery_guidance(
    label_staff_name: str,
    label_group: str,
    visitor_confirmed: bool = False,
    source: Literal["camera", "voice_correction"] = "camera",
) -> dict:
    """配達が明示された宛名を確認・照合する既存互換ツール。架空データのみ。

    氏名・所属がそろったら独自に復唱せず visitor_confirmed=False で呼び、
    返された質問を一度だけ伝える。来訪者の肯定後、同じ値で True にする。
    口頭訂正は source='voice_correction' とし、新しい値で False から確認する。
    不明な氏名・所属を補完しない。面会には get_document_reception_guidance を使う。
    """
    return _document_recipient_guidance(
        label_staff_name, label_group, visitor_confirmed, source)


def get_document_reception_guidance(
    request_type: Literal["delivery", "meeting"],
    user_utterance: str,
    document_staff_name: str,
    document_group: str,
    visitor_confirmed: bool = False,
    confirmation_utterance: str = "",
    source: Literal["camera", "voice_correction"] = "camera",
) -> dict:
    """画像の氏名・所属を一度確認し、配達または面会の受付を照合する。

    user_utterance: 来訪者が配達・会議等の用件を述べた実際の発話。画像や推測で補わない。
    document_staff_name/document_group: 読めた氏名・所属。口頭訂正なら訂正値を使う。
    visitor_confirmed=False: 独自の復唱を挟まずMCPの質問だけを一度伝える。
    visitor_confirmed=True: 同じ値・同じ用件への明確な肯定を得た場合のみ指定し、
      confirmation_utterance にその肯定発話の原文を渡す。訂正時はFalseに戻す。
    source: camera または voice_correction。宛名と面会相手が同一と推測しない。
    成功後は再確認せず message と head_motion に従って案内する。
    サーバーは会話状態を保持せず、値と肯定の対応は呼出し側で維持すること。
    """
    purpose = validate_request_type(request_type, user_utterance)
    if purpose not in ("delivery", "meeting"):
        return {"success": False, "needs_clarification": True, "reason": "purpose_unknown",
                "message": "どのような受付をご希望でしょうか？"}
    data = load_data()
    other = external_building(data, user_utterance)
    if other:
        # 別組織について自組織の名簿・画像宛名を流用しない。既存受付と同じ境界。
        result = route_result(data, other["reception_route"], request_type="external_organization")
        result.update({"facility": other["name"], "managed_by_us": False})
        return result
    fields = {"document_staff_name": document_staff_name.strip(),
              "document_group": document_group.strip()}
    missing = [key for key, value in fields.items() if not value]
    if missing:
        question = ("氏名を教えてください。" if missing == ["document_staff_name"] else
                    "所属グループを教えてください。" if missing == ["document_group"] else
                    "宛先の氏名と所属グループを教えてください。")
        return {"success": False, "needs_clarification": True, "reason": "label_incomplete",
                "missing_fields": missing, "message": question}

    spoken_name = evidence_staff_query(data, user_utterance)
    if spoken_name:
        spoken_ids = {sid for sid, _ in staff_candidates(data, spoken_name)}
        document_ids = {sid for sid, _ in staff_candidates(data, document_staff_name)}
        if not spoken_ids.intersection(document_ids):
            return {"success": False, "needs_clarification": True, "reason": "document_staff_mismatch",
                    "message": "お話のお名前と書類のお名前が一致しません。どちらの方へのご用件でしょうか？"}
    spoken_groups = matched_group_ids(data, user_utterance)
    document_groups = matched_group_ids(data, document_group)
    if spoken_groups and spoken_groups != document_groups:
        return {"success": False, "needs_clarification": True, "reason": "document_group_mismatch",
                "message": "お話の所属と書類の所属が一致しません。どちらの所属へのご用件でしょうか？"}
    if visitor_confirmed and not is_bare_confirmation(confirmation_utterance):
        return {"success": False, "needs_clarification": True,
                "reason": "document_confirmation_not_explicit",
                "message": "この氏名・所属の方へのご用件で合っていますか？"}
    result = _document_recipient_guidance(
        document_staff_name, document_group, visitor_confirmed, source, purpose)
    if result.get("reason") == "label_needs_confirmation":
        # 用途を含めて一度だけ確認し、宛名を勝手に面会相手へ流用しない。
        label = "面会" if purpose == "meeting" else "配達"
        result["message"] = f"『{document_group}の{document_staff_name}さん』への{label}で合っていますか？"
        result["confirmation_status"] = "pending"
    elif result.get("success"):
        result["confirmation_status"] = "confirmed"
    return result

def _flow_external_guidance(data: dict, utterance: str) -> dict | None:
    other = external_building(data, utterance)
    if other is None:
        return None
    result = route_result(data, other["reception_route"], request_type="external_organization")
    result.update({"facility": other["name"], "managed_by_us": False})
    return result


_reception_flow = ReceptionFlow(load_data, _document_recipient_guidance, staff_candidates,
                                matched_group_ids, evidence_staff_query, external_route=_flow_external_guidance,
                                delivery_destination=_delivery_meetup_guidance,
                                general_guidance=_general_reception_guidance, contact_lookup=_contact_guidance)


@mcp.tool()
def advance_reception(
    user_utterance: str,
    case_id: str = "",
    staff_name: str = "",
    group_name: str = "",
    source: Literal["voice", "camera", "voice_correction"] = "voice",
    document_type: str = "",
    confirmation_token: str = "",
    facility_name: str = "",
    document_context: dict | None = None,
) -> dict:
    """配達・面会・宛先の読み・電話後の行き先・建物・勤務場所の共通入口。

    user_utterance: 来訪者の実際の直近発話。画像の文字や推測を本人発話にしない。
      内線照会と訪問用件は別に管理する。内線は会議・配達の申告なしでも照会可能。
      案内後の追加質問も同じIDへ。案内終了を会話の切断・スリープと解釈しない。
    case_id: 最初は空欄。以後は返されたIDを維持。別の来訪者・別案件では必ず空欄。
      受付中の番号・読み方・電話後の行き先も同じIDへ。通常勤務場所と今回の受渡し先を混同しない。
      再接続でIDが分からなければ空欄。他の案件や過去の相手を推測して埋めない。
    staff_name/group_name: source=cameraなら画像で読めた値だけ。不明なら空欄。
      staff_nameは画像にある名字と下の名前を省略せず渡す。「森川 将大 様」なら「森川将大」。
      名だけしか読めないときはその名だけ渡し、過去の名字や名簿で画像の値を補わない。
      音声では本人発話に存在する値だけ使用する。既存の値はサーバーが保持する。
    facility_name: 画像の施設・組織名。所属グループと分け、読めなければ空欄。
      未登録の施設名を現在地に補正しない。撮影後は登録施設と宛先を一度に確認する。
    document_context: 「この人の内線は」など直近画像を参照する質問に、画像の記録漏れを補う欄。
      現在の会話で実際に取得した直近画像から、staff_name/group_name/facility_name/document_typeを指定。
      読めない欄は空欄。名簿の補完、過去案件の画像、再撮影前の古い結果は使わない。
      source=voiceで発話原文と一緒に渡す。画像情報を音声のstaff_nameへ混ぜない。
      画像を記録してから今の質問を処理し、一度の確認にまとめる。画像由来なので肯定確認は省略しない。
    document_type: parcel_label/training_notice/business_card/other/unreadable。分類だけで用件を確定しない。
    confirmation_token: action=confirm_recipientの質問を一度伝え、肯定後に返されたtokenを渡す。
      visitor_confirmed等の自己申告は不要。訂正すると古いtokenは無効。
    actionとmessageに従い質問・撮影提案・案内を行う。撮影は別の明示指示を待つ。
    successは要求処理の成否。正常な質問・確認待ち・引継ぎもtrue。案内許可とは別。
    statusは状態、next_stepは次の手順。ask_and_waitはmessageを一度質問してターン終了。
    start_preview_and_waitはプレビュー開始後に撮影指示を案内して待つ。同ターンでは撮影しない。
    guide_and_endだけhead_motionに沿って案内し、正面復帰後に終了。say_and_endは発話のみ。
    read_and_endは読取のみ、call_next_toolは指定ツールへ、endは追加動作なし。
    guidance_readyだけが案内準備済みを示す。本体の発話・動作完了や撮影許可を保証しない。
    case_summary.name_evidenceがある場合は音声の名字と画像の名から作った未確認候補。
      messageで根拠とフルネームを一度確認する。画像でフルネームを読めたとは説明しない。
    handoffならhandoff_summaryを引継ぎ情報とする。自動通知は行わない。
    guideは受付先の案内準備完了で、荷物の受領・入室許可・受付手続完了ではない。
    """
    request = {"user_utterance": user_utterance, "case_id": case_id, "staff_name": staff_name,
               "group_name": group_name, "source": source, "document_type": document_type,
               "confirmation_token": confirmation_token, "facility_name": facility_name,
               "document_context": document_context}
    return _reception_flow.advance(**request)


@mcp.tool()
def get_center_info(topic: Literal["description", "hours"] = "description") -> dict:
    """架空の「みらい技術センター」の施設情報を取得する。description=概要、hours=受付時間。"""
    data = load_data()
    facility = data["facility"]
    log(f"center_info: {topic}")
    if topic == "hours":
        message = f"{facility['name']}の受付時間は{facility['visitor_hours']}です。"
    else:
        message = facility["description"]
    return {"success": True, "facility": facility["name"], "message": message}


# Facility callbacks and the three independent public guidance tools.
from facility_tools import register_facility_tools
_facility_handlers = register_facility_tools(mcp, load_data)

# Public tools are registered explicitly; lookup helpers remain Python-only.
PUBLIC_MCP_TOOLS = frozenset({"advance_reception", "get_center_info", "get_place_guidance",
                              "get_training_guidance", "get_facility_policy"})

if __name__ == "__main__":
    mcp.run(transport="stdio")
