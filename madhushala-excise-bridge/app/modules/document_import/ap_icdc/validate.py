"""Strict, fail-closed AP ICDC reconciliation. Money calculations use Decimal."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

def money(value):
    try:
        return Decimal(str(value).replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None

def recover_cases(cases, case_rate, total):
    """Return (proposed_cases, flags). Preserve original OCR externally."""
    r, t = money(case_rate), money(total)
    if r is None or t is None or r <= 0:
        return cases, ["manual_check"]
    if cases is not None and Decimal(cases) * r == t:
        return cases, []
    candidate = t / r
    if candidate == candidate.to_integral_value() and candidate >= 0:
        return int(candidate), ["corrected_via_arithmetic", "manual_review"]
    return cases, ["manual_check"]

def validate_document(items, summary):
    checks = {}
    serials = [x.get("sl_no") for x in items]
    checks["serial_sequence"] = bool(items) and serials == list(range(1, len(items) + 1))
    row_errors = []
    review = []
    for item in items:
        n = item.get("sl_no")
        cases, rate, total = item.get("qty_cases"), money(item.get("case_rate")), money(item.get("total"))
        if cases is None or rate is None or total is None or Decimal(cases) * rate != total:
            row_errors.append(n)
        btl = money(item.get("btl_rate"))
        if btl and rate and (rate / btl - (rate / btl).to_integral_value()).copy_abs() > Decimal("0.02"):
            item.setdefault("flags", []).append("pack_ratio_ambiguous")
        if item.get("flags"):
            review.append(n)
    checks["row_arithmetic"] = not row_errors
    expected_cases = summary.get("invoice_qty_total")
    checks["case_quantity"] = expected_cases is not None and all(i.get("qty_cases") is not None for i in items) and sum(i["qty_cases"] for i in items) == expected_cases
    issue = money(summary.get("issue_price"))
    line_amounts = [money(i.get("total")) for i in items]
    checks["issue_price"] = issue is not None and all(x is not None for x in line_amounts) and sum(line_amounts, Decimal(0)) == issue
    additions = [money(summary.get(k)) for k in ("cess", "additional_previlage_fee", "additional_retail_excise_tax", "previlage_fee_clubs")]
    icdc = money(summary.get("icdc_value"))
    checks["icdc_value"] = issue is not None and icdc is not None and all(x is not None for x in additions) and issue + sum(additions, Decimal(0)) == icdc
    tcs, less = money(summary.get("tcs")), money(summary.get("less_this_invoice_value"))
    checks["less_this_invoice_value"] = icdc is not None and tcs is not None and less is not None and icdc + tcs == less
    sub, credited = money(summary.get("sub_total")), money(summary.get("amount_credited"))
    checks["amount_credited"] = sub is not None and less is not None and credited is not None and sub - less == credited
    if "challan_amount" in summary or "previous_credit_leftover" in summary:
        challan = money(summary.get("challan_amount"))
        previous = money(summary.get("previous_credit_leftover"))
        checks["funds_subtotal"] = (challan is not None and previous is not None and sub is not None
                                    and challan + previous == sub)
    status = "FAILED" if not all(checks.values()) else ("REVIEW_REQUIRED" if review else "PASSED")
    return {"status": status, "checks": checks, "row_arithmetic_errors": row_errors, "rows_needing_review": sorted(set(x for x in review if x is not None))}
