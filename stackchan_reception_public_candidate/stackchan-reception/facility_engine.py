"""架空の施設案内ルール。FastMCPと独立した純Pythonロジック。

注意: 入力はASR/VLMの推定結果。名簿や場所を推定で生成せず、
      登録済みの案内先のみ回答する。常時在席・予約確定は扱わない。
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

FACILITY_DATA_FILE = Path(__file__).resolve().parent / "facility_data.json"


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip().casefold()
    for ch in (" ", "　", "、", "。", "・", "\n", "\t"):
        value = value.replace(ch, "")
    return value


def date_agrees(document_date: str, registered_date: str) -> bool:
    """ISO日付と「2026年10月27日」「10月27日」を比較。年省略時は月日だけ確認。"""
    if norm(document_date) == norm(registered_date):
        return True
    d = unicodedata.normalize("NFKC", document_date)
    m = re.fullmatch(r"\s*(?:(\d{4})\s*[年/-]\s*)?(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?\s*", d)
    if not m:
        return False
    year, month, day = m.groups()
    ry, rm, rd = (int(x) for x in registered_date.split("-"))
    return (int(month), int(day)) == (rm, rd) and (year is None or int(year) == ry)


def _needs_check(message: str, reason: str = "unverified") -> dict:
    return {"success": False, "needs_clarification": True, "reason": reason,
            "message": message, "direction": "front", "can_point": False}


class FacilityGuide:
    def __init__(self, facility_data: dict, staff_data: dict | None = None):
        self.data = facility_data
        self.staff_data = staff_data
        self._validate()

    @classmethod
    def from_files(cls, facility_path: Path = FACILITY_DATA_FILE,
                   staff_data: dict | None = None) -> "FacilityGuide":
        with Path(facility_path).open(encoding="utf-8-sig") as fp:
            return cls(json.load(fp), staff_data)

    def _validate(self) -> None:
        d = self.data
        assert d.get("schema_version") == 1, "facility_data: unsupported schema_version"
        places = d["locations"]
        for key, p in places.items():
            if self.staff_data is not None and p["building"] not in self.staff_data["buildings"]:
                raise ValueError(f"locations.{key}: unknown building")
            if p["direction"] not in ("left", "right", "front"):
                raise ValueError(f"locations.{key}: invalid direction")
            if p["visitor_access"] not in ("public", "staff_only", "checkin_required", "external"):
                raise ValueError(f"locations.{key}: invalid visitor_access")
            if p["visitor_access"] in ("staff_only", "checkin_required"):
                if p.get("first_checkin") not in places:
                    raise ValueError(f"locations.{key}: no valid first_checkin")
            if p.get("managed_by_us") is False and p["visitor_access"] != "external":
                raise ValueError(f"locations.{key}: external boundary error")
        for sid, lid in d["staff_workplaces"].items():
            if lid not in places:
                raise ValueError(f"staff_workplaces.{sid}: unknown place")
            if self.staff_data is not None and sid not in self.staff_data["staff"]:
                raise ValueError(f"staff_workplaces.{sid}: unknown staff")
        for tid, t in d["trainings"].items():
            for name in ("venue", "first_checkin"):
                if t[name] not in places:
                    raise ValueError(f"trainings.{tid}.{name}: unknown place")
        for pid, p in d["policies"].items():
            if p["allowed_place"] not in places:
                raise ValueError(f"policies.{pid}: unknown allowed_place")
        # 同じ別名が複数の場所を指したら、推測では解決せず不正データとして止める。
        for field in ("locations", "trainings"):
            used = {}
            for key, obj in d[field].items():
                for name in (obj["name"], *obj.get("aliases", [])):
                    n = norm(name)
                    if n in used and used[n] != key:
                        raise ValueError(f"ambiguous {field} alias: {name}")
                    used[n] = key

    def _find(self, kind: str, name: str) -> tuple[str, dict] | None:
        key = norm(name)
        if not key:
            return None
        for idx, obj in self.data[kind].items():
            if key in {norm(x) for x in (obj["name"], *obj.get("aliases", []))}:
                return idx, obj
        return None

    def _place(self, key: str) -> dict:
        p = self.data["locations"][key]
        return {"place_id": key, "place_name": p["name"],
                "building": p["building"], "floor": p["floor"],
                "direction": p["direction"],
                "how_to_get_there": p["how_to_get_there"],
                "visitor_access": p["visitor_access"]}

    def place_guidance(self, place_name: str) -> dict:
        item = self._find("locations", place_name)
        if item is None:
            return _needs_check("場所の登録情報を確認できません。正面の総合受付にお尋ねください。", "place_not_found")
        lid, place = item
        final = self._place(lid)
        if place["visitor_access"] in ("staff_only", "checkin_required"):
            checkin = self._place(place["first_checkin"])
            return {"success": True, "place": final, "first_destination": checkin,
                    "direction": checkin["direction"], "can_point": True,
                    "must_checkin": True,
                    "message": f"{place['name']}は{place['how_to_get_there']} ただし来訪者は直接入れません。まず{checkin['place_name']}で受付をお願いします。"}
        if place["visitor_access"] == "external":
            return {"success": True, "place": final, "first_destination": final,
                    "direction": final["direction"], "can_point": True,
                    "managed_by_us": False, "must_checkin": True,
                    "message": f"{place['how_to_get_there']} 別組織のため、先方の職員や予定は確認できません。"}
        return {"success": True, "place": final, "first_destination": final,
                "direction": final["direction"], "can_point": True, "must_checkin": False,
                "message": place["how_to_get_there"]}

    def staff_workplace(self, staff_name: str, group_hint: str = "") -> dict:
        if self.staff_data is None:
            return _needs_check("職員名簿に接続されていません。", "staff_db_unavailable")
        from_names = self.staff_data["staff"]
        from_groups = self.staff_data["groups"]
        q = norm(staff_name)
        if not q:
            return _needs_check("お探しの方のお名前を教えてください。", "staff_name_missing")
        people = []
        for sid, s in from_names.items():
            candidates = [s["family_name"], s["family_name"]+s["given_name"],
                          s["reading"], s["family_reading"]]
            candidates += list(s.get("family_aliases", []))
            candidates += list(s.get("name_aliases", []))
            candidates += [a+s["given_name"] for a in s.get("family_aliases", [])]
            if q in {norm(x) for x in candidates}:
                # 同音異字を意図的に残す。候補が1件でも読みが同じ職員を補う。
                people.append((sid, s))
        if people:
            readings = {s["reading"] for _, s in people if q in {
                norm(s["family_name"]+s["given_name"]), norm(s["reading"]),
                *(norm(alias) for alias in s.get("name_aliases", []))}}
            fam_readings = {s["family_reading"] for _, s in people if q in (norm(s["family_name"]), norm(s["family_reading"]))}
            people = [(sid,s) for sid,s in from_names.items() if (sid,s) in people or s["reading"] in readings or s["family_reading"] in fam_readings]
        if group_hint:
            gq = norm(group_hint)
            gids = {gid for gid,g in from_groups.items() if gq in {norm(x) for x in (g["name"],*g.get("aliases",[]))}}
            if not gids:
                return _needs_check("その所属を名簿で確認できません。所属の名称をもう一度お願いします。", "group_unknown")
            people = [(sid,s) for sid,s in people if set(s["group_ids"]) & gids]
        if not people:
            return _needs_check("お名前と所属の組み合わせが名簿で確認できません。総合受付へお尋ねください。", "staff_not_found")
        if len(people) > 1:
            c = [{"staff_id":sid,"name":s["family_name"]+s["given_name"],
                  "groups":[from_groups[g]["name"] for g in s["group_ids"]]} for sid,s in people]
            return {**_needs_check("同じ読みのお名前の方が複数います。所属グループを教えてください。", "staff_ambiguous"), "candidates":c}
        sid, person = people[0]
        workplace_id = self.data["staff_workplaces"].get(sid)
        if workplace_id is None:
            return _needs_check("通常の勤務場所が登録されていません。正面の総合受付でご確認ください。", "workplace_unregistered")
        response = self.place_guidance(self.data["locations"][workplace_id]["name"])
        response.update({"staff_id": sid, "staff_name":person["family_name"]+person["given_name"],
                         "is_currently_present":None,
                         "workplace":self._place(workplace_id)})
        response["message"] = f"{response['staff_name']}さんの登録上の通常勤務場所は{response['workplace']['place_name']}です。現在の在席は確認できません。" + response["message"]
        return response


    def training_guidance(self, training_name: str, document_date: str = "",
                          document_venue: str = "", document_checkin: str = "",
                          verified_fields: str = "") -> dict:
        """画像からの読み取りは暫定情報として扱い、確証のないOCR誤読で案内を遮断しない。

        verified_fields は来訪者が明示的に確認したフィールドだけ。
        空欄（標準）はOCR由来の不確かな文字列として扱う。
        例: 'document_venue,document_date'。会話による確認なしで指定しない。
        一意に研修名を照合できた場合、案内先は常に登録データから取得する。
        """
        item = self._find("trainings", training_name)
        if item is None:
            return _needs_check(
                "研修名を登録情報と照合できませんでした。研修名の部分を大きくして撮影するか、口頭で研修名をお知らせください。",
                "training_not_found")
        tid, t = item
        all_fields = {"document_date", "document_venue", "document_checkin"}
        verified = {x.strip() for x in verified_fields.replace("、", ",").split(",") if x.strip()}
        if verified - all_fields:
            return _needs_check("確認項目が正しく指定されていません。総合受付でご確認ください。", "invalid_verified_fields")
        # 画像からの読み取りは部分的な観測。照合できない場所名や誤読は未確定とする。
        # 一方、来訪者が明示的に検証した値との矛盾は無視しない。
        observed = {
            "document_date": document_date,
            "document_venue": document_venue,
            "document_checkin": document_checkin,
        }
        discrepancies = []
        if document_date and not date_agrees(document_date, t["date"]):
            discrepancies.append("document_date")
        for field, expected in (("document_venue", t["venue"]), ("document_checkin", t["first_checkin"])):
            val = observed[field]
            if not val:
                continue
            found = self._find("locations", val)
            if found is None or found[0] != expected:
                discrepancies.append(field)
        field_labels = {"document_date": "開催日", "document_venue": "研修会場", "document_checkin": "集合・受付場所"}
        verified_conflicts = [x for x in discrepancies if x in verified]
        if verified_conflicts:
            return {
                **_needs_check("来訪者が確認した情報と登録情報の" + "・".join(field_labels[x] for x in verified_conflicts) +
                               "が一致しません。正面の総合受付で確認してください。", "document_conflict"),
                "conflicting_fields": [field_labels[x] for x in verified_conflicts],
                "training_id": tid,
                "discrepancy_fields": discrepancies,
            }
        checkin = self._place(t["first_checkin"])
        venue = self._place(t["venue"])
        return {
            "success": True, "training_id": tid, "training_name": t["name"],
            "registered_date": t["date"], "registered_time": t["time"],
            "venue": venue, "first_destination": checkin,
            "direction": checkin["direction"], "can_point": True,
            "unverified_ocr_fields": discrepancies,
            "source_of_guidance": "facility_database",
            "message": (f"{t['name']}ですね。登録情報では開催日は{t['date']}、"
                        f"会場は{venue['place_name']}です。"
                        f"まず{checkin['place_name']}で受付してください。"),
        }

    def facility_policy(self, topic: str) -> dict:
        # 関連キーワードは曖昧な自由作文を防ぐため限定する。
        alias = {"食事":"food","飲食":"food","昼食":"food","food":"food",
                 "飲み物":"drink","飲料":"drink","drink":"drink",
                 "たばこ":"smoking","煙草":"smoking","喫煙":"smoking","smoking":"smoking"}
        key = alias.get(norm(topic))
        if key is None:
            return _needs_check("その施設利用ルールは登録されていません。総合受付でご確認ください。", "policy_not_found")
        policy = self.data["policies"][key]
        place = self._place(policy["allowed_place"])
        return {"success":True, "topic":key, "first_destination":place,
                "direction":place["direction"], "can_point":True,
                "message":policy["message"] + " " + place["how_to_get_there"]}
