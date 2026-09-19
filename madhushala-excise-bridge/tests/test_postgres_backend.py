from app.postgres_backend import _translate_sql


def test_translate_qmark_placeholders_for_psycopg():
    query = _translate_sql(
        "SELECT * FROM import_items WHERE job_id=? AND mapped_item_code=?"
    )
    assert query == (
        "SELECT * FROM import_items WHERE job_id=%s AND mapped_item_code=%s"
    )


def test_translate_escapes_literal_percent_for_psycopg_parameters():
    query = _translate_sql(
        "SELECT * FROM import_items WHERE normalized_name LIKE '%whisky%' AND job_id=?"
    )
    assert query == (
        "SELECT * FROM import_items WHERE normalized_name LIKE '%%whisky%%' AND job_id=%s"
    )
