"""既存FastMCPのmcpインスタンスへ、施設案内ツールを追加する薄いアダプタ。"""
from facility_engine import FacilityGuide, norm
from document_intake import document_intake_guidance
from guidance_motion import with_head_motion


def register_facility_tools(mcp, load_staff_data):
    def guide():
        # ファイル更新をサーバーの再起動なしで読み直す。既存のload_dataはデータを検証する。
        return FacilityGuide.from_files(staff_data=load_staff_data())

    @mcp.tool()
    def get_place_guidance(place_name: str) -> dict:
        """架空施設内の場所・部屋を案内する。直接立ち入れない部屋は必ず先に受付へ誘導。
        place_name: 利用者が言った部屋名・施設名。場所は作り出さず登録名のみ照会。
        success=true の direction だけ首振り案内に用いる。
        """
        facility = guide()
        result = facility.place_guidance(place_name)
        if result.get("reason") == "place_not_found":
            data = facility.staff_data
            matches = [(bid, building) for bid, building in data["buildings"].items()
                       if norm(place_name) in {norm(name) for name in
                           (building["name"], *building.get("aliases", []))}]
            if len(matches) == 1:
                bid, building = matches[0]
                route = data["reception"].get(building.get("reception_route", ""), {})
                direction = route.get("direction")
                if building.get("wayfinding_message") and direction in ("left", "right", "front"):
                    # Building directions do not establish entry permission or a new check-in.
                    result = {"success": True, "building_id": bid, "building_name": building["name"],
                              "guidance_context": "building_wayfinding", "source": "registered_data",
                              "message": building["wayfinding_message"], "direction": direction,
                              "can_point": True, "entry_permission_confirmed": False}
        return with_head_motion(result)

    def get_staff_workplace(staff_name: str, group_hint: str = "") -> dict:
        """架空の職員の登録上の通常勤務場所を答える。現在の在席・面会予約を決めつけない。
        同音異字なら所属を確認。仕事場が見つかっても執務室への直接立入は案内しない。
        staff_name: 実際に来訪者が言った職員名。group_hint: 実際に確認できた所属、なければ空欄。
        """
        return with_head_motion(guide().staff_workplace(staff_name, group_hint))


    @mcp.tool()
    def get_training_guidance(training_name: str, document_date: str = "",
                              document_venue: str = "", document_checkin: str = "",
                              verified_fields: str = "") -> dict:
        """研修名を登録DBと照合し、最初の受付を案内する。会場と受付場所は別。

        training_name: 撮影画像や来訪者発話から読めた研修名。架空名を補わない。
        document_date/document_venue/document_checkin: OCRで実際に読めた値のみ。
          OCRの値は不確かな観測として扱う。読めなければ必ず空欄。
          研修名だけ読めたなら他は全て空欄でよい。
        verified_fields: 来訪者が特定の値を明示的に確認した場合のみ、
          'document_date,document_venue,document_checkin' のうち対応する名前を指定。
          通常の画像OCRでは必ず空文字列（省略可）。誤読と施設DBの衝突で案内を止めない。
          明示的な来訪者確認済み情報が衝突したら案内保留。
        success=true の direction だけ身体動作に利用する。
        """
        return with_head_motion(guide().training_guidance(
            training_name, document_date, document_venue, document_checkin, verified_fields))

    @mcp.tool()
    def get_facility_policy(topic: str) -> dict:
        """架空の施設内の飲食、飲料、喫煙のルールと該当場所を照会する。
        topic: 食事／飲食／昼食、飲み物、喫煙など。未登録の規則を推測しない。
        """
        return with_head_motion(guide().facility_policy(topic))

    def get_document_intake_guidance(document_type: str, user_utterance: str) -> dict:
        """画像AIの文書分類と来訪者の明示した用件を照合し、次に何を確認するかだけを返す。
        document_type: VLMが判定した parcel_label / training_notice / business_card / other / unreadable。
        user_utterance: 撮影を頼んだ際の利用者の用件発話の原文（「撮って」は除く）。LLMの補足は禁物。
        「これを読んで」だけなら配達・研修・面会を決めつけない。
        このツールは案内方向を返さず、首振り許可もしない。
        """
        return document_intake_guidance(document_type, user_utterance)

    # The reception entry can call these directly without advertising overlapping intake tools.
    return {"get_place_guidance": get_place_guidance, "get_staff_workplace": get_staff_workplace,
            "get_training_guidance": get_training_guidance, "get_facility_policy": get_facility_policy,
            "get_document_intake_guidance": get_document_intake_guidance}
