import asyncio

from app.services.mapping_service import MappingService
from app.services.matching_service import MatchIndex, score_dropdown_search, suggest_matches


def test_excise_payload_from_captured_item():
    item = {
        "brand": "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years",
        "measureMl": 750,
        "packageType": "Glass Bottle",
        "strengthRaw": "Mild",
        "retailerMargin": "120.50",
        "roundOffGovt": "0.29",
        "specialPurposeFee": "234.71",
        "mrpPerUnit": "3960.00",
        "bottlesPerCase": 6,
        "mrpPerCase": "23760.00",
        "supplier": "Westwell Gases Pvt. Ltd.",
    }

    payload = MappingService.build_excise_payload(item)

    assert payload == {
        "itemName": "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years, 750 Ml. (Glass Bottle)",
        "t1": "",
        "t2": "",
        "t3": "",
        "t4": "",
        "strengthRaw": "Mild",
        "measureMl": "750",
        "packageType": "Glass Bottle",
        "retailerMargin": "120.50",
        "roundOffGovt": "0.29",
        "specialPurposeFee": "234.71",
        "mrpPerUnit": "3960.00",
        "bottlesPerCase": "6",
        "mrpPerCase": "23760.00",
    }


def test_excise_payload_from_document_item_includes_supported_known_fields():
    item = {
        "rawName": "Some Whisky 750ml",
        "brand": "Some Whisky",
        "ml": 750,
        "packing": 12,
        "mrp": 500,
    }

    payload = MappingService.build_excise_payload(item)

    assert payload["itemName"] == "Some Whisky 750ml"
    assert payload["t1"] == ""
    assert payload["t2"] == ""
    assert payload["t3"] == ""
    assert payload["t4"] == ""
    assert payload["measureMl"] == "750"
    assert payload["mrpPerUnit"] == "500"
    assert payload["bottlesPerCase"] == "12"
    assert payload["specialPurposeFee"] == ""


def test_suggestions_put_matching_ml_and_name_on_top():
    excise_item = {
        "itemName": "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years, 750 Ml. (Glass Bottle)",
        "measureMl": 750,
        "bottlesPerCase": 12,
    }
    dropdown = [
        {"itemCode": "A00003", "itemName": "ABSOLUT VODKA 750", "ml": "750", "packing": 12},
        {"itemCode": "A00002", "itemName": "ABERFILDY 12Y 750", "ml": "750", "packing": 12},
        {"itemCode": "100003", "itemName": "100 PIPER 375ML", "ml": "375", "packing": 24},
    ]

    suggestions = suggest_matches(excise_item, dropdown)

    assert [suggestion["item"]["itemCode"] for suggestion in suggestions] == ["A00002"]
    assert all("item" in suggestion for suggestion in suggestions)


def test_suggestions_respect_different_ml_penalty():
    excise_item = {
        "itemName": "Absolut Vodka, 750 Ml. (Glass Bottle)",
        "measureMl": 750,
    }
    dropdown = [
        {"itemCode": "A00003", "itemName": "ABSOLUT VODKA 750", "ml": "750", "packing": 12},
        {"itemCode": "X00001", "itemName": "ABSOLUT VODKA 50", "ml": "50", "packing": 120},
    ]

    suggestions = suggest_matches(excise_item, dropdown)

    assert suggestions[0]["item"]["itemCode"] == "A00003"


def test_suggestions_extract_ml_from_unmapped_name_with_age_number():
    excise_item = {
        "itemName": "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years, 750 Ml. (Glass Bottle)",
    }
    dropdown = [
        {"itemCode": "A00002", "itemName": "ABERFILDY 12Y 750", "ml": "750", "packing": 12},
        {"itemCode": "X00012", "itemName": "ABERFILDY 12Y 180", "ml": "180", "packing": 48},
    ]

    suggestions = suggest_matches(excise_item, dropdown)

    assert suggestions[0]["item"]["itemCode"] == "A00002"


def test_suggestions_do_not_use_same_ml_without_name_match():
    excise_item = {
        "itemName": "Absolut Vodka, 750 Ml. (Glass Bottle)",
        "measureMl": 750,
    }
    dropdown = [
        {"itemCode": "R00001", "itemName": "ROYAL GREEN WHISKY 750", "ml": "750", "packing": 12},
        {"itemCode": "A00003", "itemName": "ABSOLUT VODKA 750", "ml": "750", "packing": 12},
    ]

    suggestions = suggest_matches(excise_item, dropdown)

    assert [suggestion["item"]["itemCode"] for suggestion in suggestions] == ["A00003"]


def test_auto_process_capture_without_token_needs_token():
    service = MappingService()
    capture = {"itemCount": 2, "items": []}

    status = asyncio.run(service.auto_process_capture(capture, ""))

    assert status["state"] == "needs_token"
    assert status["mappingRequired"] is False
    assert "token" in status["message"].lower()


def test_excise_item_save_happens_before_unmapped_lookup():
    events = []

    class FakeClient:
        async def save_excise_item(self, payload):
            events.append("save")
            return {}

        async def get_unmapped_items(self):
            events.append("unmapped")
            return [{"exciseItemCode": 55, "itemName": "Test Item"}]

    service = MappingService()
    code, action, _ = asyncio.run(
        service._create_or_reuse_excise_item(
            FakeClient(),
            {"itemName": "Test Item"},
            [],
        )
    )

    assert events == ["save", "unmapped"]
    assert code == 55
    assert action == "submitted_resolved"


def test_workspace_filters_full_unmapped_api_to_latest_capture_items(tmp_path):
    class FakeClient:
        async def get_unmapped_items(self):
            return [
                {"exciseItemCode": 101, "itemName": "Old Item, 750 Ml. (Glass Bottle)"},
                {"exciseItemCode": 303, "itemName": "Existing Item, 750 Ml. (Glass Bottle)"},
                {"exciseItemCode": 202, "itemName": "Fresh Item, 750 Ml. (Glass Bottle)"},
            ]

        async def get_dropdown_items(self, company_code, bill_type):
            return [
                {"itemCode": "F00001", "itemName": "FRESH ITEM 750", "ml": "750", "packing": 12},
            ]

        async def save_excise_item(self, payload):
            # Madhushala owns de-duplication and returns the existing/new Excise code.
            if str(payload.get("itemName", "")).startswith("Existing Item"):
                return {"itemCode": 303, "itemName": payload["itemName"]}
            return {"itemCode": 202, "itemName": payload["itemName"]}

    service = MappingService()
    service.state_path = str(tmp_path / "mapping_state.json")
    service._client = lambda token: FakeClient()
    capture = {
        "batchId": "latest-batch",
        "itemCount": 2,
        "items": [
            {
                "canonicalKey": "existing-750-glass",
                "brand": "Existing Item",
                "measureMl": 750,
                "packageType": "Glass Bottle",
                "mrpPerUnit": "100.00",
                "supplier": "Supplier",
            },
            {
                "canonicalKey": "fresh-750-glass",
                "brand": "Fresh Item",
                "measureMl": 750,
                "packageType": "Glass Bottle",
                "mrpPerUnit": "100.00",
                "supplier": "Supplier",
            }
        ],
    }

    asyncio.run(service.prepare_latest_capture(capture, "token"))
    workspace = asyncio.run(service.workspace("token", capture=capture))

    assert [item["exciseItemCode"] for item in workspace["unmappedItems"]] == [303, 202]
    assert workspace["latestCaptureCount"] == 2

    all_workspace = asyncio.run(service.workspace("token", capture=capture, latest_only=False))

    assert [item["exciseItemCode"] for item in all_workspace["unmappedItems"]] == [101, 303, 202]
    assert all_workspace["latestOnly"] is False


def test_dropdown_search_scores_initials_and_prefixes():
    absolut = {"itemCode": "A00003", "itemName": "ABSOLUT VODKA 750", "ml": "750"}
    aberfeldy = {"itemCode": "A00002", "itemName": "ABERFILDY 12Y 750", "ml": "750"}
    tuborg = {"itemCode": "T00001", "itemName": "TUBORG 650", "ml": "500"}
    unrelated = {"itemCode": "X00001", "itemName": "ROYAL GREEN 750", "ml": "750"}

    assert score_dropdown_search(absolut, "av") > score_dropdown_search(aberfeldy, "av")
    assert score_dropdown_search(aberfeldy, "a") > score_dropdown_search(tuborg, "a")
    assert score_dropdown_search(absolut, "A00003") > score_dropdown_search(aberfeldy, "A00003")
    assert score_dropdown_search(unrelated, "a") == 0


def test_dropdown_search_requires_meaningful_word_match():
    old_monk = {"itemCode": "O00001", "itemName": "OLD MONK RUM 750", "ml": "750"}
    royal_green = {"itemCode": "R00001", "itemName": "ROYAL GREEN WHISKY 750", "ml": "750"}
    after_dark = {"itemCode": "A00004", "itemName": "AFTER DARK BLUE 750", "ml": "750"}

    assert score_dropdown_search(old_monk, "om") > score_dropdown_search(royal_green, "om")
    assert score_dropdown_search(after_dark, "after") > score_dropdown_search(old_monk, "after")
    assert score_dropdown_search(royal_green, "aft") == 0


def test_excise_reuse_identity_keeps_same_name_different_ml_separate():
    items = [
        {"exciseItemCode": 101, "itemName": "McDowell's No. 1 Superior Whisky", "measureMl": "180"},
        {"exciseItemCode": 102, "itemName": "McDowell's No. 1 Superior Whisky", "measureMl": "750"},
        {"exciseItemCode": 201, "itemName": "McDowells No.1 Luxury Blended Whisky", "measureMl": "180"},
        {"exciseItemCode": 202, "itemName": "McDowells No.1 Luxury Blended Whisky", "measureMl": "375"},
        {"exciseItemCode": 203, "itemName": "McDowells No.1 Luxury Blended Whisky", "measureMl": "750"},
    ]

    by_identity, by_name = MappingService._unmapped_indexes(items)

    assert MappingService._find_existing_excise(
        {"itemName": "McDowell's No. 1 Superior Whisky", "measureMl": "180"},
        by_identity,
        by_name,
    )["exciseItemCode"] == 101

    assert MappingService._find_existing_excise(
        {"itemName": "McDowell's No. 1 Superior Whisky", "measureMl": "750"},
        by_identity,
        by_name,
    )["exciseItemCode"] == 102

    assert MappingService._find_existing_excise(
        {"itemName": "McDowells No.1 Luxury Blended Whisky", "measureMl": "180"},
        by_identity,
        by_name,
    )["exciseItemCode"] == 201

    assert MappingService._find_existing_excise(
        {"itemName": "McDowells No.1 Luxury Blended Whisky", "measureMl": "375"},
        by_identity,
        by_name,
    )["exciseItemCode"] == 202

    assert MappingService._find_existing_excise(
        {"itemName": "McDowells No.1 Luxury Blended Whisky", "measureMl": "750"},
        by_identity,
        by_name,
    )["exciseItemCode"] == 203


def test_excise_reuse_never_falls_back_to_name_when_ml_differs():
    items = [
        {"exciseItemCode": 101, "itemName": "Same Whisky", "measureMl": "180"},
        {"exciseItemCode": 102, "itemName": "Same Whisky", "measureMl": "750"},
    ]
    by_identity, by_name = MappingService._unmapped_indexes(items)

    assert MappingService._find_existing_excise(
        {"itemName": "Same Whisky", "measureMl": "375"},
        by_identity,
        by_name,
    ) is None


def test_company_item_codes_uses_fresh_company_catalogue(monkeypatch):
    from app.services import mapping_service as mapping_module

    calls = {"count": 0}

    async def fake_fresh_catalogue(session):
        calls["count"] += 1
        assert session["company_code"] == "2"
        return [
            {"itemCode": "M00001", "itemName": "ONE"},
            {"itemCode": "M00002", "itemName": "TWO"},
        ]

    monkeypatch.setattr(mapping_module.reference_data_service, "fresh_catalogue", fake_fresh_catalogue)

    service = MappingService()
    codes = asyncio.run(
        service.company_item_codes(
            {"shop_code": "SHOP", "company_code": "2", "bill_type": "AI"}
        )
    )

    assert calls["count"] == 1
    assert codes == {"M00001", "M00002"}


def test_indexed_suggestions_preserve_best_match_and_reduce_candidates():
    dropdown = [
        {"itemCode": "A00002", "itemName": "ABERFILDY 12Y 750", "ml": "750", "packing": 12},
        {"itemCode": "A00003", "itemName": "ABSOLUT VODKA 750", "ml": "750", "packing": 12},
        {"itemCode": "R00001", "itemName": "ROYAL GREEN WHISKY 750", "ml": "750", "packing": 12},
    ] + [
        {"itemCode": f"X{index:05d}", "itemName": f"UNRELATED PRODUCT {index} 180", "ml": "180", "packing": 48}
        for index in range(500)
    ]
    excise_item = {
        "itemName": "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years, 750 Ml. (Glass Bottle)",
        "measureMl": 750,
        "bottlesPerCase": 12,
    }

    index = MatchIndex(dropdown)
    candidates = index.candidates(excise_item)
    indexed = suggest_matches(excise_item, dropdown, index=index)
    full_scan = suggest_matches(excise_item, dropdown)

    assert len(candidates) < len(dropdown) / 10
    assert indexed[0]["item"]["itemCode"] == "A00002"
    assert indexed == full_scan


def test_match_index_falls_back_when_no_index_signal_exists():
    dropdown = [
        {"itemCode": "A1", "itemName": "ALPHA", "ml": ""},
        {"itemCode": "B1", "itemName": "BETA", "ml": ""},
    ]
    index = MatchIndex(dropdown)

    candidates = index.candidates({"itemName": "ZZ"})

    assert candidates == dropdown


def test_prepare_document_job_uses_bounded_concurrency(monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    from app.services import mapping_service as mapping_module

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE import_jobs(
            id TEXT, shop_code TEXT, session_id TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE import_items(
            id TEXT, job_id TEXT, raw_name TEXT, brand TEXT, ml INTEGER,
            packing INTEGER, mrp REAL, rate REAL, barcode TEXT,
            mapped_item_code TEXT, excise_item_code TEXT,
            mapping_status TEXT, created_at TEXT, updated_at TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE mappings_v2(
            shop_code TEXT, company_code TEXT, excise_item_code TEXT,
            madhushala_item_code TEXT
        )
        """
    )
    db.execute("INSERT INTO import_jobs VALUES (?,?,?)", ("job-1", "SHOP", "session-1"))
    for index in range(12):
        db.execute(
            """
            INSERT INTO import_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"row-{index}",
                "job-1",
                f"ITEM {index}",
                f"ITEM {index}",
                750,
                12,
                100,
                90,
                "",
                "",
                "",
                "PENDING",
                f"2026-09-20T00:00:{index:02d}+00:00",
                "",
            ),
        )
    db.commit()

    @contextmanager
    def fake_conn():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(mapping_module, "conn", fake_conn)

    service = MappingService()
    active = {"count": 0, "max": 0}

    async def fake_prepare(_client, payload, _unmapped):
        active["count"] += 1
        active["max"] = max(active["max"], active["count"])
        try:
            await asyncio.sleep(0.02)
            return payload["itemName"].replace("ITEM ", ""), "submitted", []
        finally:
            active["count"] -= 1

    monkeypatch.setattr(service, "_create_or_reuse_excise_item", fake_prepare)

    result = asyncio.run(
        service.prepare_document_job(
            {
                "shop_code": "SHOP",
                "session_id": "session-1",
                "company_code": "2",
                "bill_type": "AI",
                "madhushala_token": "token",
            },
            "job-1",
        )
    )

    assert result["preparedCount"] == 12
    assert active["max"] > 1
    assert active["max"] <= mapping_module.settings.DOCUMENT_PREPARE_CONCURRENCY
    saved = db.execute(
        "SELECT COUNT(*) AS total FROM import_items WHERE mapping_status='UNMAPPED'"
    ).fetchone()
    assert saved["total"] == 12
