"""
Brand and package type normalization utilities
"""
import re
import unicodedata
from decimal import Decimal


def normalize_brand(brand):
    """
    Normalize brand names:
    - Unicode normalize
    - trim
    - collapse whitespace
    - casefold
    - normalize apostrophes
    - remove unnecessary repeated punctuation
    """
    if not brand:
        return ""

    # Unicode normalize
    normalized = unicodedata.normalize("NFKC", brand)

    # Casefold
    normalized = normalized.casefold()

    # Normalize apostrophes
    normalized = normalized.replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")

    # Remove unnecessary repeated punctuation (but keep single apostrophes)
    normalized = re.sub(r"([^\w\s'])\1+", r"\1", normalized)

    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized)

    # Trim
    return normalized.strip()


def normalize_package_type(package):
    """Normalize package type strings"""
    if not package:
        return ""

    normalized = unicodedata.normalize("NFKC", package)
    normalized = normalized.casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def parse_ml(value):
    """Parse ML value from string, returns 0 if invalid"""
    if not value:
        return 0

    try:
        return int(unicodedata.normalize("NFKC", str(value)).strip())
    except (ValueError, TypeError):
        return 0


def parse_decimal(value):
    """Parse decimal value from string, returns None if invalid"""
    if not value:
        return None

    try:
        normalized = unicodedata.normalize("NFKC", str(value)).strip()
        return Decimal(normalized)
    except (ValueError, TypeError, ArithmeticError):
        return None


def parse_int(value):
    """Parse integer value from string, returns 0 if invalid"""
    if not value:
        return 0

    try:
        return int(unicodedata.normalize("NFKC", str(value)).strip())
    except (ValueError, TypeError):
        return 0
</parameter>
</write_to_file>