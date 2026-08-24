import asyncio

from app.services.mapping_service import MappingService
from app.services.matching_service import score_dropdown_search, suggest_matches


def test_excise_payload_from_captured_item():
    item = {
        "brand": "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years",
        "measureMl": 750,
        "packageType": "Glass Bottle",
        "mrpPerUnit": "3960.00",
        "supplier": "Westwell Gases Pvt. Ltd.",
    }

    payload = MappingService.build_excise_payload(item)

    assert payload == {
        "itemName": "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years, 750 Ml. (Glass Bottle)",
        "t1": "750",
        "t2": "3960.00",
        "t3": "Glass Bottle",
        "t4": "Westwell Gases Pvt. Ltd.",
    }


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

    assert suggestions[0]["item"]["itemCode"] == "A00002"
    assert suggestions[0]["score"] > suggestions[1]["score"]
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
