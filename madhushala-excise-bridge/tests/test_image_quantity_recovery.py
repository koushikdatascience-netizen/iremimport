from app.modules.document_import.image_quantity_recovery import recover_image_quantities
from app.modules.document_import.schemas import ExtractedDocument


def test_recovers_mumbai_rows_from_identified_quantity_cells():
    doc = ExtractedDocument(items=[
        {"itemName": "KINGFISHER STRONG CAN BEER", "ml": "500ml"},
        {"itemName": "LONDON PILSNER STRONG BEER", "ml": "650ml"},
    ])
    evidence = {"items": [
        {"itemName": "KINGFISHER STRONG CAN BEER", "ml": 500, "caseHeader": "CS", "caseCell": "5", "bottleHeader": "BT", "bottleCell": "0"},
        {"itemName": "LONDON PILSNER STRONG BEER", "ml": 650, "caseHeader": "CS", "caseCell": "5", "bottleHeader": "BT", "bottleCell": "0"},
    ]}
    result, count = recover_image_quantities(doc, evidence)
    assert count == 2
    assert [(item.box, item.loose) for item in result.items] == [(5, 0), (5, 0)]


def test_does_not_override_or_guess_ambiguous_rows():
    doc = ExtractedDocument(items=[{"itemName": "Beer A", "ml": 500, "box": 2},
                                   {"itemName": "Beer B", "ml": 650}])
    evidence = {"items": [{"itemName": "Beer A", "ml": 500, "caseHeader": "CS", "caseCell": "5"},
                          {"itemName": "Beer B", "ml": 650, "caseHeader": "RATE", "caseCell": "5"}]}
    result, count = recover_image_quantities(doc, evidence)
    assert count == 0
    assert result.items[0].box == 2
    assert result.items[1].box is None
    evidence["items"][1]["caseHeader"] = "CS"
    evidence["items"].append(dict(evidence["items"][1]))
    result, count = recover_image_quantities(doc, evidence)
    assert count == 0
    assert result.items[1].box is None
