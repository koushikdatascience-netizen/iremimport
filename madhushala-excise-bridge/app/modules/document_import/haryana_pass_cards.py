"""Parse official Haryana PassDetails brand cards without a browser or third-party HTML parser."""
from __future__ import annotations

import re
from html.parser import HTMLParser


class _PassCards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.active: dict | None = None
        self.card_depth = -1
        self.in_p = False
        self.p_parts: list[str] = []
        self.brands: list[dict[str, str]] = []
        self.receipt: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            self.depth += 1
            classes = dict(attrs).get("class", "").split()
            if self.active is None and ("brand-card" in classes or "receipt-card" in classes):
                self.active = {"kind": "brand" if "brand-card" in classes else "receipt", "data": {}}
                self.card_depth = self.depth
        if tag == "p" and self.active is not None:
            self.in_p = True
            self.p_parts = []

    def handle_data(self, data):
        if self.in_p and self.active is not None:
            self.p_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p and self.active is not None:
            line = " ".join("".join(self.p_parts).split())
            if ":" in line:
                key, value = line.split(":", 1)
                self.active["data"][key.strip().casefold().rstrip(".")] = value.strip()
            self.in_p = False
        if tag == "div":
            if self.active is not None and self.depth == self.card_depth:
                if self.active["kind"] == "brand" and self.active["data"]:
                    self.brands.append(self.active["data"])
                elif self.active["kind"] == "receipt":
                    self.receipt.update(self.active["data"])
                self.active = None
            self.depth = max(0, self.depth - 1)


def haryana_card_payload(html: str) -> dict | None:
    """Return the existing table-parser payload shape; never invent missing quantities.

    Haryana's official portal currently renders each brand in a .brand-card
    with <p><span>label:</span> value</p>, rather than a <table>.
    """
    parser = _PassCards()
    parser.feed(html)
    if not parser.brands:
        return None
    labels = ("Brand Name", "Pack Type", "Number of Cases",
              "Number of Loose Bottles", "Batch Number")
    rows = [list(labels)]
    for brand in parser.brands:
        name = brand.get("brand name")
        pack = brand.get("packing type") or brand.get("pack type")
        cases = brand.get("no. of cases") or brand.get("no of cases")
        loose = brand.get("loose bottles") or brand.get("number of loose bottles")
        if not name or not pack or cases is None or loose is None:
            # Drop incomplete cards rather than create misleading amounts/quantities.
            continue
        parsed_pack = re.fullmatch(r"\s*(\d{2,5})\s*\(\s*(\d{1,3})\s*\)\s*(?:m\.?l\.?)?\s*", pack, re.I)
        if not parsed_pack or int(parsed_pack.group(1)) <= 0 or int(parsed_pack.group(2)) <= 0:
            continue
        if not re.fullmatch(r"\d+", cases) or not re.fullmatch(r"\d+", loose):
            continue
        rows.append([name, f"{parsed_pack.group(1)}({parsed_pack.group(2)})",
                     cases, loose, brand.get("batch number", "")])
    if len(rows) == 1 or len(rows) - 1 != len(parser.brands):
        return None
    pass_no = parser.receipt.get("pass number") or parser.receipt.get("pass no", "")
    permit_no = parser.receipt.get("permit number") or parser.receipt.get("permit no", "")
    if not pass_no or not permit_no:
        return None
    metadata = f"Pass No: {pass_no} Permit Number: {permit_no}"
    return {"title": "Haryana Pass Details", "text": metadata, "tables": [rows],
            "sourceState": "HARYANA", "permitNumber": permit_no, "passNumber": pass_no}
