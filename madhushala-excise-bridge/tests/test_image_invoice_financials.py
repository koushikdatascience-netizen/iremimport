from decimal import Decimal
from io import BytesIO

from PIL import Image

from app.modules.document_import.image_invoice_financials import reconcile
from app.modules.document_import.schemas import ExtractedDocument
from test_document_import import auth, client, create_session  # noqa: F401


def values():
    return {key: Decimal(value) for key, value in {
        'grossAmount': '27427.25', 'discount': '0', 'cashDiscount': '274.27',
        'freight': '120', 'tpFees': '5', 'auxCharges': '0', 'subTotal': '27277.98',
        'vat': '0', 'tcsEcSc': '545.56', 'adjustment': '0', 'roundedOff': '.46', 'totalNet': '27824',
    }.items()}


def test_printed_image_summary_reconciles_and_rejects_shifted_or_missing_cells():
    amounts = [Decimal('17730.75'), Decimal('9696.50')]
    assert reconcile(values(), amounts)
    assert not reconcile(values(), [amounts[1], amounts[1]])
    wrong = values()
    wrong['vat'] = None
    assert not reconcile(wrong, amounts)
    wrong = values()
    wrong['totalNet'] += 1
    assert not reconcile(wrong, amounts)


def test_image_verified_financials_reach_handoff(client, monkeypatch):
    from app.db import conn
    from app.modules.document_import import purchase_required
    from app.modules.document_import.service import DocumentImportService
    from app.services.reference_data_service import reference_data_service
    financials = {key: float(value) for key, value in values().items()}
    financials.update(totalAmount=27427.25, reconciled=True, taxes={'vat': 0, 'tcsEcSc': 545.56},
                     validation={'status': 'verified'}, calculationMappingRequired=['tcsEcSc'])
    doc = ExtractedDocument(documentType='invoice', invoiceNumber='TEST-IMAGE', items=[
        {'itemName': 'Image Beer A', 'ml': 500, 'box': 5, 'loose': 0, 'invoiceAmount': 17730.75,
         'sourceState': 'MAHARASHTRA', 'sourceFinancialValidation': {'status': 'verified'}, 'invoiceFinancials': financials},
        {'itemName': 'Image Beer B', 'ml': 650, 'box': 5, 'loose': 0, 'invoiceAmount': 9696.50,
         'sourceState': 'MAHARASHTRA', 'sourceFinancialValidation': {'status': 'verified'}, 'invoiceFinancials': financials},
    ])
    async def extract(*args, **kwargs): return doc, {'engine': 'verified-image-financials'}
    async def context(*args, **kwargs): return {'defaults': {}}
    async def catalogue(*args, **kwargs): return [{'itemCode': 'IMG0', 'packing': 24}, {'itemCode': 'IMG1', 'packing': 12}]
    monkeypatch.setattr(DocumentImportService, '_extract_upload_part', extract)
    monkeypatch.setattr(purchase_required, 'build_purchase_context', context)
    monkeypatch.setattr(reference_data_service, 'fresh_catalogue', catalogue)
    session = create_session(client)
    source = BytesIO()
    Image.new('RGB', (20, 20), 'white').save(source, 'JPEG')
    response = client.post('/api/v1/document-import/upload/batch/image', headers=auth(session), files=[('files', ('invoice.jpg', source.getvalue(), 'image/jpeg'))])
    assert response.status_code == 200, response.text
    job = response.json()['job']['id']
    rows = client.get(f'/api/v1/document-import/jobs/{job}/items', headers=auth(session)).json()['items']
    assert [row['amount'] for row in rows] == [17730.75, 9696.5]
    with conn() as db:
        for i, row in enumerate(rows):
            db.execute("UPDATE import_items SET mapped_item_code=?,mapping_status='MAPPED' WHERE id=?", (f'IMG{i}', row['id']))
    response = client.get(f'/api/v1/document-import/jobs/{job}/purchase/handoff', headers=auth(session))
    assert response.status_code == 200, response.text
    purchase = response.json()['purchasePayload']
    assert {item['itemName']: (item['box'], item['amount'], item['itemAmount']) for item in purchase['items']} == {
        'Image Beer A': (5, 17730.75, 17730.75), 'Image Beer B': (5, 9696.5, 9696.5),
    }
    assert purchase['taxes']['vat'] == 0
    assert purchase['taxes']['tcsEcSc'] == 545.56
    assert purchase['taxes']['tcs'] is None
    assert purchase['totalAmount'] == 27427.25 and purchase['totalNet'] == 27824
    assert purchase['roundedOff'] == .46
    assert purchase['sourceFinancials']['cashDiscount'] == 274.27
