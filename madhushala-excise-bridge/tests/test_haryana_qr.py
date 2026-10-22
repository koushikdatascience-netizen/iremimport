from app.modules.document_import.haryana_qr import pass_id_from_url, parse_pass_html

URL = "https://portal.haryanatax.gov.in/Department/QRGeneration/PassDetails?passheaderid=1010603"

def test_haryana_qr_link():
    assert pass_id_from_url(URL) == "1010603"

def test_haryana_qr_strict_host_and_parameter():
    bad = [
        "http://portal.haryanatax.gov.in/Department/QRGeneration/PassDetails?passheaderid=1010603",
        "https://portal.haryanatax.gov.in.evil.org/Department/QRGeneration/PassDetails?passheaderid=1010603",
        "https://portal.haryanatax.gov.in/Department/QRGeneration/PassDetails?passheaderid=1&url=evil",
        "https://portal.haryanatax.gov.in/Department/QRGeneration/PassDetails?passheaderid=abc",
        "https://127.0.0.1/Department/QRGeneration/PassDetails?passheaderid=1010603",
    ]
    assert all(pass_id_from_url(x) is None for x in bad)

def test_haryana_explicit_quantity_table():
    html = """<table><tr><th>Brand Name</th><th>Size (ML)</th>
    <th>Cases</th><th>Qty (Bottles)</th></tr>
    <tr><td>Sample Premium Whisky</td><td>750</td><td>5</td><td>2</td></tr>
    </table>"""
    doc = parse_pass_html(html, "1010603")
    assert len(doc.items)==1
    assert doc.items[0].itemName=="Sample Premium Whisky"
    assert doc.items[0].ml==750
    assert doc.items[0].box==5
    assert doc.items[0].loose==2

def test_haryana_ambiguous_qty_rejected():
    html = """<table><tr><th>Brand Name</th><th>Size (ML)</th><th>Qty</th></tr>
    <tr><td>Sample Premium Whisky</td><td>750</td><td>5</td></tr></table>"""
    assert parse_pass_html(html,"1010603").items == []
