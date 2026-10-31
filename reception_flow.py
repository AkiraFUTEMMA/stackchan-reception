"""受付の用件証拠と、一件の確認・訂正を扱う。音声/画像AIや実機とは独立。"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import re
from threading import RLock
from time import monotonic
import unicodedata
from uuid import uuid4


def without_denied_purposes(text: str) -> str:
    """句内の『XではなくY』『Xではありません』を保守的に除く。一般的な日本語解析ではない。"""
    text = unicodedata.normalize("NFKC", text or "")
    # 『だけではなく』はXも肯定している。否定として捨てない。
    if re.search(r"(?:だけ|のみ)(?:ではなく|じゃなく)", text):
        return text
    return re.sub(
        r"[^、。！？!?\n]*(?:ではありません|じゃありません|ではなくて?|じゃなくて?|でなくて?|ではない|じゃない)(?:です)?",
        "", text)


def _norm(text: str) -> str:
    return re.sub(r"[\s、。！？!?]", "", unicodedata.normalize("NFKC", text or ""))


def _document_reference(text: str) -> bool:
    return any(word in _norm(text) for word in ("この人", "その人", "この方", "その方", "この写真", "その写真",
                                               "この画像", "さっきの写真", "今の写真", "この宛名", "この書類"))


def purpose_hits(text: str) -> set[str]:
    text = _norm(text)
    words = {
        "delivery": ("荷物", "配達", "宅配", "届け", "納品", "お預け"),
        "meeting": ("会議", "打ち合わせ", "打合せ", "面談", "面会", "約束", "アポイント", "訪ねたい", "会いに来"),
        "other_building": ("研修", "講習", "別棟", "別の建物"),
    }
    return {kind for kind, terms in words.items() if any(word in text for word in terms)}


def _affirmative(text: str) -> bool:
    text = re.sub(r"^(?:ああ?|えっと|ええと|えーと|あの)+", "", _norm(text))
    return text in {
        "はい", "はいそうです", "そうです", "合っています", "合ってます", "はい合ってます",
        "はい合っています", "そのとおりです", "その通りです", "間違いありません", "ええそうです",
        "はいそれでいいと思います", "合ってると思います", "はい合ってると思います",
        "いいです", "はいいいです", "それでいいです", "はいそれでいいです",
    }


def _response_control(action: str) -> tuple[str, str]:
    if action.startswith("ask_"):
        return "waiting", "ask_and_wait"
    return {
        "confirm_recipient": ("waiting", "ask_and_wait"),
        "offer_document": ("waiting", "ask_and_wait"),
        "prepare_camera": ("waiting", "start_preview_and_wait"),
        "guide": ("ready", "guide_and_end"),
        "repeat_contact": ("answered", "say_and_end"),
        "answer_contact": ("answered", "say_and_end"),
        "repeat_guidance": ("answered", "say_and_end"),
        "explain_delivery": ("answered", "say_and_end"),
        "read_only": ("answered", "read_and_end"),
        "handoff": ("handoff", "handoff_and_end"),
        "use_facility_tool": ("routing", "call_next_tool"),
        "done": ("done", "end"),
    }.get(action, ("error", "say_and_end"))


def _input_error(reason, message, action="error"):
    return {"success": False, "status": "error", "next_step": "ask_and_wait",
            "guidance_ready": False, "action": action, "reason": reason, "message": message}


def is_read_only_request(text: str) -> bool:
    """文書に現れる用件語と、本人が述べた受付の目的を分ける。"""
    text = _norm(without_denied_purposes(text))
    if not any(word in text for word in ("読んで", "読み取", "何て書", "なんて書")):
        return False
    explicit_visit = re.search(
        r"届け(?:たい|に来|ます)|配達(?:です|に来)|納品(?:です|に来)|荷物(?:です|を預け)|"
        r"(?:会議|面会|打合せ|打ち合わせ)(?:です|に来|の約束)|会いに来|訪ねたい|研修に参加|研修の受付", text)
    return explicit_visit is None


@dataclass
class _Case:
    case_id: str
    touched: float
    purpose: str = "unknown"
    information_request: str = ""
    reported_name: str = ""
    voice_full_name: str = ""
    staff_name: str = ""
    group_name: str = ""
    facility_name: str = ""
    confirmed_facility_name: str = ""
    meetup_facility: str = ""
    general_query: dict = field(default_factory=dict)
    name_evidence: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    last_action: str = ""
    document_attempted: bool = False
    document_offered: bool = False
    correction_offered: bool = False
    pending_token: str = ""
    pending_key: tuple = ()
    confirmed_key: tuple = ()
    guided_key: tuple = ()
    last_guidance: str = ""
    last_guidance_key: tuple = ()
    unknown_fields: set = field(default_factory=set)
    unresolved: set = field(default_factory=set)
    conflicts: set = field(default_factory=set)
    binding_required: bool = False

    def key(self):
        return (self.purpose, self.staff_name, self.group_name, self.facility_name, self.reported_name)

    def guidance_key(self):
        return (*self.key(), self.meetup_facility)

    def invalidate(self):
        self.confirmed_key, self.pending_key, self.pending_token, self.guided_key = (), (), "", ()
        self.confirmed_facility_name = ""
        self.meetup_facility = ""
        self.general_query = {}
        self.last_guidance, self.last_guidance_key = "", ()


class ReceptionFlow:
    """IDを明示して続ける短期案件。再起動で消失し、期限切れIDは再利用しない。

    旧MCP APIはこの状態の対象外。案件IDは本人認証ではなく、呼出し側は来訪者ごとに
    新規案件を開始する必要がある。外部への通知・音声合成・撮影・首振りは実行しない。
    """

    def __init__(self, load_data, resolve, find_staff, find_groups, extract_name,
                 *, ttl_seconds=1800, max_cases=128, clock=monotonic, external_route=None,
                 delivery_destination=None, general_guidance=None, contact_lookup=None):
        if ttl_seconds <= 0 or max_cases < 1:
            raise ValueError("Positive case TTL and capacity are required")
        self.load_data, self.resolve = load_data, resolve
        self.find_staff, self.find_groups, self.extract_name = find_staff, find_groups, extract_name
        self.ttl_seconds, self.max_cases, self.clock = ttl_seconds, max_cases, clock
        self.external_route = external_route
        self.delivery_destination = delivery_destination
        self.general_guidance = general_guidance
        self.contact_lookup = contact_lookup
        self._cases = OrderedDict()
        self._lock = RLock()

    def _reply(self, case, action, message="", **extra):
        case.last_action = action
        if action == "guide":
            case.last_guidance, case.last_guidance_key = message, case.guidance_key()
        elif action == "ask_correction":
            # A correction request revokes the previous directions, including public queries.
            case.last_guidance, case.last_guidance_key = "", ()
        summary = {"purpose": case.purpose, "information_request": case.information_request,
                   "reported_name": case.reported_name, "staff_name": case.staff_name,
                   "group_name": case.group_name, "facility_name": case.facility_name,
                   "confirmed_facility_name": case.confirmed_facility_name,
                   "meetup_facility": case.meetup_facility,
                   "general_query": dict(case.general_query),
                   "sources": dict(case.sources),
                   "recipient_confirmed": case.confirmed_key == case.key(),
                   "unresolved_fields": sorted(case.unresolved | case.conflicts)}
        if case.name_evidence:
            summary["name_evidence"] = dict(case.name_evidence)
        if case.voice_full_name:
            summary["voice_full_name"] = case.voice_full_name
        status, next_step = _response_control(action)
        result = {"case_id": case.case_id, "action": action,
                  "needs_clarification": action.startswith("ask_") or action == "confirm_recipient",
                  "message": message, "case_summary": summary, "reception_completed": False, **extra}
        # Processing succeeded even when the correct result is to ask, wait or hand off.
        # Guidance readiness is separate; these control fields cannot be overridden by route data.
        result.update(success=status != "error", status=status, next_step=next_step,
                      action=action, needs_clarification=next_step == "ask_and_wait",
                      guidance_ready=action == "guide" and status == "ready")
        if not result["guidance_ready"]:
            result.pop("head_motion", None)
        return result

    def _handoff(self, case, reason, message=None):
        missing = [key for key in ("staff_name", "group_name") if not getattr(case, key)]
        summary = {"purpose": case.purpose, "staff_name": case.staff_name,
                   "group_name": case.group_name, "facility_name": case.facility_name,
                   "missing_fields": missing,
                   "recipient_confirmed": case.confirmed_key == case.key(),
                   "reason": reason, "remaining_work": "未確認事項と受付手順を担当者が確認"}
        return self._reply(case, "handoff", message or "特定に必要な情報がそろわないため、正面の総合受付でご相談ください。",
                           reason=reason, handoff_summary=summary)

    def _confirm(self, case, data, explain_reading=False):
        if case.pending_key != case.key() or not case.pending_token:
            case.pending_key, case.pending_token = case.key(), uuid4().hex
        purpose = "配達" if case.purpose == "delivery" else "面会"
        gids = self.find_groups(data, case.group_name)
        matches = [person for _, person in self.find_staff(data, case.staff_name)
                   if set(person["group_ids"]) & gids
                   or (not person["group_ids"] and "__CENTER__" in gids)]
        reading = matches[0]["reading"] if len(matches) == 1 else ""
        spoken_name = reading or case.staff_name
        prefix = "名簿では、" if case.sources.get("group_name") == "database" else ""
        site = f"ここは『{data['facility']['name']}』です。" if case.document_attempted or case.facility_name else ""
        destination = "こちらの" if site else ""
        explanation = (f"読み取った氏名は『{case.staff_name}』、名簿の登録読みは『{reading}』です。"
                       if explain_reading and reading else "")
        if case.name_evidence:
            parts = case.name_evidence
            explanation = (f"お話の名字『{parts['voice_family_name']}』と画像の下のお名前"
                           f"『{parts['camera_given_name']}』から、名簿の『{case.staff_name}』様が候補です。")
        if case.reported_name and len(matches) == 1:
            person = matches[0]
            explanation = (f"伺った『{case.reported_name}』は名簿の表記と一致しません。"
                           f"候補は『{person['family_name'] + person['given_name']}』様です。")
        question = (f"『{case.group_name}の{spoken_name}さん』の内線番号の照会で合っていますか？"
                    if case.information_request == "contact" else
                    f"『{case.group_name}の{spoken_name}さん』への{purpose}で合っていますか？")
        return self._reply(case, "confirm_recipient",
                           f"{explanation}{site}{prefix}{destination}{question}",
                           confirmation_token=case.pending_token, recipient_reading=reading)

    def _camera_name(self, case, data, observed):
        """Combine exact name parts in one case, never infer a missing surname from the directory."""
        previous_source = case.sources.get("staff_name")
        family = (case.staff_name if previous_source in ("voice", "voice_correction") else
                  case.name_evidence.get("voice_family_name", "") if previous_source == "voice+camera" else "")
        candidates = self.find_staff(data, family) if family and observed else []
        family_only = any(_norm(family) in {
            _norm(person["family_name"]), _norm(person["family_reading"]),
            *(_norm(alias) for alias in person.get("family_aliases", []))} for _, person in candidates)
        names = {person["family_name"] + person["given_name"] for _, person in candidates
                 if _norm(observed) == _norm(person["given_name"])} if family_only else set()
        if len(names) == 1:
            name = names.pop()
            return name, {"voice_family_name": family, "camera_given_name": observed,
                          "matched_staff_name": name}
        return observed, {}

    def advance(self, user_utterance, case_id="", staff_name="", group_name="",
                source="voice", document_type="", confirmation_token="", facility_name="", document_context=None):
        if source not in ("voice", "camera", "voice_correction"):
            return _input_error("invalid_source", "入力元を確認できません。")
        if document_context is not None:
            limits = {"staff_name": 256, "group_name": 256, "facility_name": 256, "document_type": 40}
            if (source == "camera" or not _document_reference(user_utterance)
                    or not isinstance(document_context, dict) or not document_context
                    or any(key not in limits or not isinstance(value, str) or len(value) > limits[key]
                           for key, value in document_context.items())):
                return _input_error("invalid_document_context", "参照する画像の情報を確認できません。")
        if any(len(value) > limit for value, limit in (
            (user_utterance, 4000), (case_id, 64), (staff_name, 256),
            (group_name, 256), (facility_name, 256), (document_type, 40), (confirmation_token, 64))):
            return _input_error("input_too_long", "入力を短くしてお伝えください。")
        # Camera fields are observations, never a replayed human utterance or a new purpose.
        if source == "camera":
            user_utterance = ""
        with self._lock:
            now = self.clock()
            for key in list(self._cases):
                if now - self._cases[key].touched >= self.ttl_seconds:
                    del self._cases[key]
            if case_id:
                case = self._cases.get(case_id)
                if case is None:
                    return _input_error("case_not_found", "前の受付情報を確認できません。新しい会話で、知りたいこととお相手を教えてください。", "restart")
            else:
                while len(self._cases) >= self.max_cases:
                    self._cases.popitem(last=False)
                case = _Case(uuid4().hex, now)
                self._cases[case.case_id] = case
            case.touched = now
            self._cases.move_to_end(case.case_id)
            if document_context is not None:
                # Record the explicitly supplied observation and answer the current voice request
                # under one lock. Never infer this person's identity from a previous visitor's case.
                self._advance(case, "", document_context.get("staff_name", ""),
                              document_context.get("group_name", ""), "camera",
                              document_context.get("document_type", ""), "", document_context.get("facility_name", ""))
            return self._advance(case, user_utterance, staff_name, group_name,
                                 source, document_type, confirmation_token, facility_name)

    def _advance(self, case, utterance, staff_name, group_name, source, document_type, token, facility_name):
        previous_action, before_key = case.last_action, case.key()
        previous_purpose = case.purpose
        positive = purpose_hits(without_denied_purposes(utterance))
        raw = _norm(utterance)
        destination_denied = any(_norm(clause) in {
            "ここではありません", "こちらではありません", "いいえここではありません",
            "いいえこちらではありません", "別の施設です", "別の施設宛てです"}
            for clause in re.split(r"[、。！？!?\n]", utterance))
        data = self.load_data()
        reading_question = ((any(word in raw for word in ("下の名前", "読み方", "どう読む", "なんて読む"))
                             and any(word in raw for word in ("ですか", "かね", "でしょう", "かな", "わかん", "分か", "じゃない")))
                            or ("これ" in raw and "じゃないの" in raw))
        evidence = without_denied_purposes(utterance)
        contact_words = ("内線", "電話番号")
        contact_text = _norm(evidence)
        if re.search(r"(?:この|その)(?:人|方)の内戦(?:番号)?(?:は|を教えて(?:ください)?|を知りたい|わかりますか)$", contact_text):
            contact_text = contact_text.replace("内戦", "内線")
        if source != "camera":
            if (any(word in contact_text for word in contact_words)
                    and not is_read_only_request(utterance)
                    and not re.search(r"(?:内線|電話番号).*(?:不要|[い要][らり](?:ない|ません)|"
                                      r"(?:教え|調べ|案内し|答え|知らせ)(?:ないで|なくて))", raw)):
                case.information_request = "contact"
                if case.purpose == "read_only":
                    case.purpose = "unknown"
            elif any(word in raw for word in contact_words):
                case.information_request = ""
        reading_groups = self.find_groups(data, evidence)
        reading_name = self.extract_name(data, evidence)
        spoken_name = staff_name.strip() if staff_name and _norm(staff_name) in _norm(evidence) else ""
        if not spoken_name and reading_name and source != "camera":
            match = re.search(re.escape(reading_name) + r"[一-龠々ぁ-んァ-ヶー]{1,12}(?:さん|様)", evidence)
            if match:
                spoken_name = re.sub(r"(?:さん|様)$", "", match.group())
        unmatched_spoken = (spoken_name if source != "camera" and reading_name
                            and self.find_staff(data, reading_name) and spoken_name
                            and not self.find_staff(data, spoken_name) else "")
        if (source == "voice" and not any((staff_name, group_name, facility_name))
                and not case.information_request and case.confirmed_key == case.key()
                and raw in {"ありがとう", "ありがとうございます", "わかりました", "分かりました", "了解です", "はい"}):
            return self._reply(case, "done")
        if (source == "voice" and not any((staff_name, group_name, facility_name))
                and not case.information_request and raw in {
                    "どこ", "どこですか", "どこ行けばいいですか", "どこに行けばいいですか", "行き先は", "場所は"}):
            if case.last_guidance and case.last_guidance_key == case.guidance_key():
                return self._reply(case, "repeat_guidance", case.last_guidance)
            return self._reply(case, "ask_destination", "どちらへの行き方をお調べしますか？")
        same_reading_person = (not reading_name or bool(
            {sid for sid, _ in self.find_staff(data, reading_name)}
            & {sid for sid, _ in self.find_staff(data, case.staff_name)}))
        if (source == "voice" and case.document_attempted and case.pending_token
                and case.pending_key == case.key() and reading_question and not case.conflicts
                and not any((staff_name, group_name, facility_name))
                and (not positive or positive == {case.purpose})
                and not (case.purpose in purpose_hits(utterance) and case.purpose not in positive)
                and not is_read_only_request(utterance)
                and not destination_denied and same_reading_person
                and (not reading_groups or reading_groups == self.find_groups(data, case.group_name))):
            return self._confirm(case, data, explain_reading=True)
        if (self.delivery_destination and source == "voice" and case.purpose == "delivery"
                and case.guided_key == case.key() and not case.conflicts
                and not any((staff_name, group_name, facility_name))
                and positive <= {"delivery", "other_building"}
                and not is_read_only_request(utterance)
                and not any(word in raw for word in ("研修に参加", "研修を受け", "講習を受け", "受講",
                                                      "研修の受付", "配達は終", "配達は終了", "別件",
                                                      "読み取りだけ", "読むだけ"))):
            named = self.extract_name(data, without_denied_purposes(utterance))
            current_ids = {sid for sid, _ in self.find_staff(data, case.staff_name)}
            groups = self.find_groups(data, without_denied_purposes(utterance))
            group_agrees = not groups or groups == self.find_groups(data, case.group_name)
            name_agrees = not named or current_ids & {sid for sid, _ in self.find_staff(data, named)}
            if name_agrees and group_agrees:
                followup = self.delivery_destination(data, utterance, case.meetup_facility, case.staff_name)
                if followup:
                    action = followup.get("action", "guide")
                    if action == "guide":
                        if case.meetup_facility == followup["facility"]:
                            action = "repeat_guidance"
                            followup.pop("head_motion", None)
                        case.meetup_facility = followup["facility"]
                    elif followup.get("reason") == "meetup_unconfirmed":
                        case.meetup_facility = ""
                    return self._reply(case, action, **{key: value for key, value in followup.items()
                                                       if key not in ("success", "action")})
        if (self.general_guidance and source == "voice" and not case.information_request
                and (case.purpose == "unknown" and not case.document_attempted
                     or case.confirmed_key == case.key()
                     or (any(word in raw for word in ("どこ", "行き方", "場所"))
                         and not any(word in raw for word in ("電話", "言われ", "受け取", "受渡", "来て"))))
                and not any((staff_name, group_name, facility_name))
                and not unmatched_spoken and not destination_denied
                and (not case.staff_name or not reading_name or same_reading_person)
                and (not case.group_name or not reading_groups
                     or reading_groups == self.find_groups(data, case.group_name))
                and not positive & {"delivery", "meeting"}
                and not is_read_only_request(utterance)):
            general = self.general_guidance(data, evidence, case.general_query)
            if general:
                case.general_query = general.pop("query_context", {})
                action = general.get("action", "guide" if general.get("success") else "ask_correction")
                return self._reply(case, action, **{key: value for key, value in general.items() if key not in ("success", "action")})
        if len(positive) == 1:
            case.purpose = next(iter(positive))
        elif len(positive) > 1:
            case.purpose = "unknown"
        elif case.purpose in purpose_hits(utterance):
            # 用件が否定されたが、新しい用件がまだ指定されていない。
            case.purpose = "unknown"
        reading_only = any(word in raw for word in ("読み取りだけ", "読取だけ", "読むだけ", "文字だけ", "受付は不要", "案内は不要"))
        if is_read_only_request(utterance) and (not case.information_request or reading_only) and (previous_purpose in ("unknown", "read_only")
                                                or case.purpose == "unknown" or reading_only):
            case.purpose = "read_only"
            case.information_request = ""

        if case.purpose == "external_organization" and case.guided_key == case.key():
            return self._reply(case, "done")

        if case.purpose != "read_only" and self.external_route:
            external = self.external_route(data, utterance)
            if external:
                case.purpose, case.staff_name, case.group_name = "external_organization", "", ""
                case.facility_name = ""
                case.sources.clear()
                case.name_evidence.clear()
                case.voice_full_name = ""
                case.invalidate()
                case.guided_key = case.key()
                return self._reply(case, "guide", **{key: value for key, value in external.items() if key not in ("success", "message")},
                                   message=external["message"], guidance_ready=True, human_followup_required=True)
        incoming, incoming_name_evidence, incoming_reported_name = {}, {}, ""
        if source == "camera":
            case.document_attempted = True
            observed_site, observed_group = facility_name.strip(), group_name.strip()
            case.conflicts.discard("facility_name")
            # Compatibility for an OCR caller that put a centre name in the department field.
            # Unknown departments are otherwise retained, never silently replaced by the directory.
            if (observed_group and not self.find_groups(data, observed_group)
                    and (observed_group == observed_site or _norm(observed_group).endswith("センター"))):
                if observed_site and _norm(observed_site) != _norm(observed_group):
                    case.conflicts.add("facility_name")
                observed_site = observed_site or observed_group
                observed_group = ""
            case.facility_name = observed_site
            if observed_site:
                case.sources["facility_name"] = "camera"
            else:
                case.sources.pop("facility_name", None)
            observed_name, incoming_name_evidence = self._camera_name(case, data, staff_name.strip())
            incoming = {"staff_name": observed_name, "group_name": observed_group}
            # 再撮影で読めなかった欄を、古い未確認OCRで補わない。
            for field_name, value in incoming.items():
                if not value and field_name == "staff_name" and case.voice_full_name:
                    # An unread retake drops old OCR, not an independently spoken identity.
                    case.staff_name = case.voice_full_name
                    case.sources["staff_name"] = "voice"
                    case.name_evidence.clear()
                if not value and field_name == "staff_name" and case.sources.get(field_name) == "voice+camera":
                    case.staff_name = case.name_evidence["voice_family_name"]
                    case.sources["staff_name"] = "voice"
                    case.name_evidence.clear()
                    if case.sources.get("group_name") == "database":
                        case.group_name = ""
                        case.sources.pop("group_name", None)
                if not value and case.sources.get(field_name) == "camera":
                    setattr(case, field_name, "")
                    case.sources.pop(field_name, None)
        else:
            evidence = without_denied_purposes(utterance)
            evidence_norm = _norm(evidence)
            if facility_name and _norm(facility_name) in evidence_norm:
                case.facility_name = facility_name.strip()
                case.sources["facility_name"] = source
                case.conflicts.discard("facility_name")
            family_hits, full_hits = set(), set()
            for person in data["staff"].values():
                families = [person["family_name"], person["family_reading"], *person.get("family_aliases", [])]
                if any(_norm(name) in evidence_norm for name in families):
                    family_hits.add(person["family_reading"])
                full_names = [family + person["given_name"] for family in families]
                full_names += [person["reading"], person["family_name"] + person["reading"][len(person["family_reading"]):]]
                full_names += person.get("name_aliases", [])
                if any(_norm(name) in evidence_norm for name in full_names):
                    full_hits.add(person["reading"])
            if len(family_hits) > 1 or len(full_hits) > 1:
                case.staff_name = ""
                case.name_evidence.clear()
                case.voice_full_name = ""
                case.sources.pop("staff_name", None)
                if case.sources.get("group_name") == "database":
                    case.group_name = ""
                    case.sources.pop("group_name", None)
                case.unresolved.add("staff_name")
                case.invalidate()
                return self._reply(case, "ask_name", "ご用件のお相手を一人ずつ教えてください。")
            name = self.extract_name(data, evidence)
            if not name and case.staff_name and previous_action == "ask_name":
                # A given-name answer is meaningful only within the current surname's candidates.
                answer = evidence_norm
                for prefix in ("下の名前は", "下の名前が", "名前は", "名は"):
                    if prefix in answer:
                        answer = answer.split(prefix, 1)[1]
                        break
                for suffix in ("です", "さん", "様"):
                    if answer.endswith(suffix):
                        answer = answer[:-len(suffix)]
                given_matches = []
                for _, person in self.find_staff(data, case.staff_name):
                    reading = person["reading"]
                    family_reading = person["family_reading"]
                    given_reading = reading[len(family_reading):] if reading.startswith(family_reading) else ""
                    if answer and answer in {_norm(person["given_name"]), _norm(given_reading)}:
                        given_matches.append(person)
                if len(given_matches) == 1:
                    person = given_matches[0]
                    name = person["family_name"] + person["given_name"]
            if not name and staff_name and _norm(staff_name) in evidence_norm:
                name = staff_name.strip()
            if unmatched_spoken:
                incoming_reported_name = unmatched_spoken
                case.binding_required = True
            groups = self.find_groups(data, evidence)
            if len(groups) > 1:
                case.group_name = ""
                case.sources.pop("group_name", None)
                case.unresolved.add("group_name")
                case.invalidate()
                return self._reply(case, "ask_group", "所属グループを一つ確認させてください。")
            group = ""
            if groups:
                gid = next(iter(groups))
                group = "センター付" if gid == "__CENTER__" else data["groups"][gid]["name"]
            elif group_name and _norm(group_name) in evidence_norm:
                group = group_name.strip()
            incoming = {"staff_name": name, "group_name": group}

        for field_name, value in incoming.items():
            if not value:
                continue
            current = getattr(case, field_name)
            previous_source = case.sources.get(field_name)
            if source == "camera" and ((field_name == "staff_name" and case.voice_full_name)
                                       or current and case.sources.get(field_name) in ("voice", "voice_correction", "voice+camera")):
                if field_name == "staff_name":
                    voice_name = case.voice_full_name or case.name_evidence.get("voice_family_name", current)
                    agrees = bool({sid for sid, _ in self.find_staff(data, voice_name)} &
                                  {sid for sid, _ in self.find_staff(data, value)})
                else:
                    agrees = self.find_groups(data, current) == self.find_groups(data, value)
                if not agrees:
                    case.conflicts.add(field_name)
                    case.invalidate()
                    return self._reply(case, "ask_correction", "お話と画像の情報が一致しません。正しい氏名または所属を教えてください。")
            case.unresolved.discard(field_name)
            case.conflicts.discard(field_name)
            case.unknown_fields.discard(field_name)
            if value != current:
                if field_name == "staff_name" and current:
                    if case.sources.get("group_name") == "database":
                        case.group_name = ""
                        case.sources.pop("group_name", None)
                    elif case.group_name and not incoming.get("group_name"):
                        case.binding_required = True
                setattr(case, field_name, value)
                case.sources[field_name] = source
            if field_name == "staff_name":
                if incoming_reported_name:
                    case.reported_name = incoming_reported_name
                    case.voice_full_name = ""
                elif source == "camera" or value != current:
                    case.reported_name = ""
                if source != "camera" and not incoming_reported_name:
                    people = self.find_staff(data, value)
                    full_names = {full for _, person in people for full in (
                        person["family_name"] + person["given_name"], person["reading"],
                        person["family_name"] + person["reading"][len(person["family_reading"]):],
                        *person.get("name_aliases", []),
                        *(alias + person["given_name"] for alias in person.get("family_aliases", [])))}
                    if _norm(value) in {_norm(full) for full in full_names}:
                        case.voice_full_name = value
                    elif case.voice_full_name and not (
                            {sid for sid, _ in people} & {sid for sid, _ in self.find_staff(data, case.voice_full_name)}):
                        case.voice_full_name = ""
                if source == "camera":
                    case.name_evidence = incoming_name_evidence
                    if incoming_name_evidence:
                        case.sources[field_name] = "voice+camera"
                    elif value != current or previous_source not in ("voice", "voice_correction"):
                        case.sources[field_name] = "camera"
                else:
                    # An explicitly spoken full name is evidence even when its value is unchanged.
                    case.name_evidence.clear()
                    case.sources[field_name] = source
        if case.key() != before_key:
            case.invalidate()

        # Negative destination evidence revokes an earlier guide too; it needs no positive token.
        if case.purpose in ("delivery", "meeting") and source != "camera" and destination_denied:
            case.conflicts.add("facility_name")
            case.invalidate()
            return self._handoff(case, "destination_facility_denied")
        if case.conflicts:
            return self._reply(case, "ask_correction", "情報の不一致が残っています。正しい施設名・氏名・所属を教えてください。")
        if "staff_name" in case.unresolved:
            return self._reply(case, "ask_name", "ご用件のお相手を一人ずつ教えてください。")
        if "group_name" in case.unresolved:
            return self._reply(case, "ask_group", "所属グループを一つ確認させてください。")

        if case.purpose == "read_only":
            return self._reply(case, "read_only", "読み取れた文字だけを伝えてください。受付案内は行いません。")
        if case.purpose == "unknown" and not case.information_request:
            return self._reply(case, "ask_purpose", "どのようなご用件でしょうか？")
        if case.purpose == "other_building" and not case.information_request:
            return self._reply(case, "use_facility_tool", next_tool="get_training_guidance")
        if source != "camera" and any(word in raw for word in ("読んで", "読み取")):
            # A document request within an existing delivery is still that delivery.
            # Start a new observation/confirmation cycle, including for a retake.
            case.invalidate()
            return self._reply(case, "prepare_camera", "書類を映す準備をしてください。撮影は明示的な指示を待ちます。")
        if case.guided_key == case.key() and not case.information_request:
            if case.purpose == "delivery" and (any(word in raw for word in ("内線", "電話番号"))
                                               or re.fullmatch(r"\d{2,6}(?:なん|何|だっけ|でしたっけ)", raw)):
                contact_result = self.resolve(case.staff_name, case.group_name, True,
                                              "voice_correction", case.purpose)
                contact = contact_result.get("contact") if contact_result.get("success") else None
                if contact:
                    return self._reply(case, "repeat_contact",
                                       f"{contact['staff_name']}様の内線は、{contact['extension_reading']}、です。",
                                       contact=contact, human_followup_required=True)
            if raw in {"", "ありがとう", "ありがとうございます", "わかりました", "分かりました", "了解です", "はい"}:
                return self._reply(case, "done")
            return self._reply(case, "ask_followup", "どの点を確認したいですか？")
        unknown = any(word in raw for word in ("分かりません", "わかりません", "知らない", "わからない", "分からない",
                                              "わかんな", "分かんな", "わからなく", "分からなく"))
        if previous_action == "ask_name":
            reading_evidence = _norm(without_denied_purposes(utterance))
            unknown |= (any(word in reading_evidence for word in ("名前", "漢字", "宛名", "読み方"))
                        and any(word in reading_evidence for word in ("読めない", "読めません", "読み方がちょっと")))
        no_document = any(word in raw for word in ("ありません", "持っていない", "持ってない", "書類はない", "ないです"))
        if previous_action == "offer_document" and (unknown or no_document):
            return self._handoff(case, "no_more_evidence")
        if previous_action == "offer_document" and any(word in raw for word in ("あります", "持ってます", "持っています", "はい")):
            return self._reply(case, "prepare_camera", "書類を映す準備をしてください。撮影は明示的な指示を待ちます。")
        if (previous_action in ("ask_group", "ask_name") and unknown
                and not incoming.get("group_name" if previous_action == "ask_group" else "staff_name")):
            missing_field = "group_name" if previous_action == "ask_group" else "staff_name"
            case.unknown_fields.add(missing_field)
            if case.document_offered or case.document_attempted:
                return self._handoff(case, "identity_unresolved")
            case.document_offered = True
            return self._reply(case, "offer_document", "宛先の氏名や所属が書かれた書類はお持ちですか？")

        if not case.staff_name:
            if "staff_name" in case.unknown_fields and case.document_attempted:
                return self._handoff(case, "name_unread_and_unknown")
            message = ("この会話では内線の相手を確認できません。どなたの内線番号をお調べしますか？"
                       if case.information_request == "contact" else "どなたへのご用件でしょうか？")
            return self._reply(case, "ask_name", message, missing_fields=["staff_name"])

        candidates = self.find_staff(data, case.staff_name)
        if not candidates:
            if case.correction_offered and unknown:
                return self._handoff(case, "staff_unresolved")
            case.correction_offered = True
            return self._reply(case, "ask_correction", "お名前を名簿で確認できません。書類を撮るか、氏名を口頭で教えてください。")
        if case.group_name and not self.find_groups(data, case.group_name):
            if case.correction_offered and unknown:
                return self._handoff(case, "group_unrecognized")
            case.correction_offered = True
            return self._reply(case, "ask_correction", "所属グループを名簿で確認できません。施設名とは分けて、所属を口頭で訂正するか再撮影してください。")
        if not case.group_name:
            given_names = {person["given_name"] for _, person in candidates}
            family_names = {person["family_name"] for _, person in candidates}
            if len(candidates) > 1 and len(given_names) > 1 and case.staff_name in family_names:
                if case.reported_name and case.information_request == "contact":
                    return self._reply(case, "ask_group", "氏名の表記を名簿で確認できません。所属は分かりますか？",
                                       missing_fields=["group_name"])
                if "staff_name" in case.unknown_fields and case.document_attempted:
                    return self._handoff(case, "name_unread_and_unknown")
                return self._reply(case, "ask_name", "同じ苗字の方が複数います。下のお名前か所属は分かりますか？",
                                   missing_fields=["staff_name"])
            if len(candidates) > 1 or len(candidates[0][1]["group_ids"]) > 1:
                if "group_name" in case.unknown_fields and case.document_attempted:
                    return self._handoff(case, "group_unread_and_unknown")
                return self._reply(case, "ask_group", "所属グループを教えてください。", missing_fields=["group_name"])
            person = candidates[0][1]
            gid = person["group_ids"][0] if person["group_ids"] else "__CENTER__"
            case.group_name = "センター付" if gid == "__CENTER__" else data["groups"][gid]["name"]
            case.sources["group_name"] = "database"

        needs_confirmation = ("camera" in case.sources.values() or case.document_attempted
                              or case.binding_required or bool(case.facility_name))
        if needs_confirmation and case.confirmed_key != case.key():
            if (token and token == case.pending_token and case.pending_key == case.key()
                    and _affirmative(utterance) and source != "camera"):
                case.confirmed_key = case.key()
                if case.document_attempted or case.facility_name:
                    case.confirmed_facility_name = data["facility"]["name"]
            elif token and any(word in raw for word in ("違", "合っていません", "合ってない", "いいえ")):
                case.pending_key, case.pending_token = (), ""
                return self._reply(case, "ask_correction", "違う箇所を教えてください。")
            else:
                return self._confirm(case, data)

        if case.information_request == "contact":
            # Older Python callers supplied only a reception resolver. Use its contact data,
            # never its route/motion, without changing the case's actual visit purpose.
            result = (self.contact_lookup(data, case.staff_name, case.group_name) if self.contact_lookup else
                      self.resolve(case.staff_name, case.group_name, True, "voice_correction", "delivery"))
            if result.get("success") and not result.get("contact"):
                return self._handoff(case, "extension_unregistered", "登録された内線番号を確認できません。総合受付でご確認ください。")
            if not result.get("success") or not result.get("contact"):
                if result.get("reason") == "extension_unregistered":
                    return self._handoff(case, "extension_unregistered", result["message"])
                return self._reply(case, "ask_correction", result.get("message", "登録された内線番号を確認できません。"),
                                   reason=result.get("reason", "extension_unregistered"))
            action = "repeat_contact" if case.guided_key == case.key() else "answer_contact"
            case.information_request = ""
            case.confirmed_key = case.key()
            contact = result["contact"]
            return self._reply(case, action, f"{contact['staff_name']}様の内線は、{contact['extension_reading']}、です。", contact=contact,
                               answered_request="contact", human_followup_required=True)

        result = self.resolve(case.staff_name, case.group_name, True, "camera" if needs_confirmation else "voice_correction", case.purpose)
        if not result.get("success"):
            if case.correction_offered and unknown:
                return self._handoff(case, result.get("reason", "recipient_unresolved"))
            case.correction_offered = True
            return self._reply(case, "ask_correction", result["message"], reason=result.get("reason"))
        case.guided_key, case.confirmed_key = case.key(), case.key()
        return self._reply(case, "guide", **{key: value for key, value in result.items() if key not in ("success", "message")},
                           message=result["message"], guidance_ready=True,
                           human_followup_required=True if case.purpose == "meeting" or result.get("contact") else None)
