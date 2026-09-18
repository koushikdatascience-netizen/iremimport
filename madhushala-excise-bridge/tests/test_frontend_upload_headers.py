from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"


def test_formdata_upload_does_not_force_json_content_type():
    source = APP_JS.read_text(encoding="utf-8")

    assert 'options.body instanceof FormData' in source
    assert 'if (!isFormData && options.body != null && !hasContentType)' in source
    assert 'headers["Content-Type"] = "application/json";' in source

    batch_call = source.split(
        'api("/api/v1/document-import/upload/batch/" + encodeURIComponent(kind)',
        1,
    )[1].split("if (!payload?.job)", 1)[0]
    assert "body: form" in batch_call
    assert "Content-Type" not in batch_call
