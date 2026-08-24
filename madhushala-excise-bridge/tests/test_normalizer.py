"""
Test cases for Madhushala Excise Bridge normalizer functions - Phase 1
"""
import pytest
import asyncio
import re
from app.services.normalizer import (
    normalize_brand,
    normalize_package_type,
    parse_ml,
    parse_decimal,
    parse_int
)
from decimal import Decimal

class TestBrandNormalization:
    """Test brand normalization functions"""

    def test_basic_normalization(self):
        """Test basic brand normalization"""
        assert normalize_brand("Absolut Vodka") == "absolut vodka"
        assert normalize_brand("JOHNNIE WALKER") == "johnnie walker"
        assert normalize_brand("  Extra Spaces  ") == "extra spaces"

    def test_unicode_normalization(self):
        """Test Unicode character normalization"""
        assert normalize_brand("BKD's Red Neat Premium Whisky") == "bkd's red neat premium whisky"
        assert normalize_brand("BKD\u2019s Red Neat Premium Whisky") == "bkd's red neat premium whisky"
        assert normalize_brand("BKD\u02bcS Red Neat Premium Whisky") == "bkd's red neat premium whisky"

    def test_apostrophe_variants(self):
        """Test different apostrophe variants"""
        assert normalize_brand("BKD's") == "bkd's"
        assert normalize_brand("BKD\u2019s") == "bkd's"
        assert normalize_brand("BKD\u2018s") == "bkd's"
        assert normalize_brand("BKD\u02bcs") == "bkd's"

    def test_whitespace_collapsing(self):
        """Test whitespace collapsing"""
        assert normalize_brand("Aberfeldy  Single   Highland  Malt") == "aberfeldy single highland malt"
        assert normalize_brand("Aberfeldy\tSingle\nMalt") == "aberfeldy single malt"

    def test_empty_string(self):
        """Test empty string handling"""
        assert normalize_brand("") == ""
        assert normalize_brand("   ") == ""

    def test_special_characters(self):
        """Test special characters are preserved appropriately"""
        assert normalize_brand("BKD's & Sons") == "bkd's & sons"
        assert normalize_brand("BKD's - Premium") == "bkd's - premium"

class TestPackageTypeNormalization:
    """Test package type normalization"""

    def test_basic_package_normalization(self):
        """Test basic package type normalization"""
        assert normalize_package_type("Glass Bottle") == "glass bottle"
        assert normalize_package_type("PET Bottle") == "pet bottle"
        assert normalize_package_type("  Can  ") == "can"

    def test_whitespace_normalization(self):
        """Test whitespace normalization in package types"""
        assert normalize_package_type("Glass  Bottle") == "glass bottle"
        assert normalize_package_type("PET\tBottle") == "pet bottle"

    def test_empty_package(self):
        """Test empty package type handling"""
        assert normalize_package_type("") == ""
        assert normalize_package_type("   ") == ""

class TestMLParsing:
    """Test ML value parsing"""

    def test_valid_ml_values(self):
        """Test valid ML value parsing"""
        assert parse_ml("750") == 750
        assert parse_ml("50") == 50
        assert parse_ml("1000") == 1000
        assert parse_ml("750 ML") == 750
        assert parse_ml("750ml") == 750

    def test_invalid_ml_values(self):
        """Test invalid ML value parsing"""
        assert parse_ml("") == 0
        assert parse_ml("abc") == 0
        assert parse_ml("750ML") == 750
        assert parse_ml("N/A") == 0

    def test_edge_cases(self):
        """Test edge cases for ML parsing"""
        assert parse_ml("0") == 0
        assert parse_ml("1") == 1
        assert parse_ml("9999") == 9999

class TestDecimalParsing:
    """Test decimal value parsing"""

    def test_valid_decimal_values(self):
        """Test valid decimal value parsing"""
        assert parse_decimal("3960.00") == Decimal("3960.00")
        assert parse_decimal("2020.50") == Decimal("2020.50")
        assert parse_decimal("243.06") == Decimal("243.06")
        assert parse_decimal("9.98") == Decimal("9.98")

    def test_invalid_decimal_values(self):
        """Test invalid decimal value parsing"""
        assert parse_decimal("") is None
        assert parse_decimal("abc") is None
        assert parse_decimal("N/A") is None

    def test_currency_formats(self):
        """Test currency format parsing"""
        assert parse_decimal("₹3960.00") == Decimal("3960.00")
        assert parse_decimal("3,960.00") == Decimal("3960.00")

class TestIntParsing:
    """Test integer value parsing"""

    def test_valid_int_values(self):
        """Test valid integer value parsing"""
        assert parse_int("6") == 6
        assert parse_int("12") == 12
        assert parse_int("0") == 0
        assert parse_int("100") == 100

    def test_invalid_int_values(self):
        """Test invalid integer value parsing"""
        assert parse_int("") == 0
        assert parse_int("abc") == 0
        assert parse_int("N/A") == 0

    def test_edge_cases(self):
        """Test edge cases for integer parsing"""
        assert parse_int("0") == 0
        assert parse_int("1") == 1
        assert parse_int("9999") == 9999

class TestCanonicalKeyGeneration:
    """Test canonical key generation logic"""

    def test_canonical_key_format(self):
        """Test canonical key format"""
        from app.services.capture_service import CaptureService
        service = CaptureService()

        # Test same brand, different ML produces different keys
        item1 = {
            "brand": "Absolut Vodka",
            "measureMl": "750",
            "packageType": "Glass Bottle"
        }

        item2 = {
            "brand": "Absolut Vodka",
            "measureMl": "50",
            "packageType": "Glass Bottle"
        }

        normalized1 = service._normalize_item(item1)
        normalized2 = service._normalize_item(item2)

        assert normalized1["canonicalKey"] == "absolut vodka|750|glass bottle"
        assert normalized2["canonicalKey"] == "absolut vodka|50|glass bottle"
        assert normalized1["canonicalKey"] != normalized2["canonicalKey"]

    def test_same_product_different_mrp(self):
        """Test that same product with different MRP produces same canonical key"""
        from app.services.capture_service import CaptureService
        service = CaptureService()

        item1 = {
            "brand": "Absolut Vodka",
            "measureMl": "750",
            "packageType": "Glass Bottle",
            "mrpPerUnit": "2000.00"
        }

        item2 = {
            "brand": "Absolut Vodka",
            "measureMl": "750",
            "packageType": "Glass Bottle",
            "mrpPerUnit": "2020.00"
        }

        normalized1 = service._normalize_item(item1)
        normalized2 = service._normalize_item(item2)

        assert normalized1["canonicalKey"] == normalized2["canonicalKey"]
        assert normalized1["canonicalKey"] == "absolut vodka|750|glass bottle"

class TestFullNormalization:
    """Test full item normalization"""

    def test_complete_item_normalization(self):
        """Test complete item normalization"""
        from app.services.capture_service import CaptureService
        service = CaptureService()

        raw_item = {
            "brand": "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years",
            "strengthRaw": "( 40 %V/V - WB/GEN )",
            "measureMl": "750",
            "packageType": "Glass Bottle",
            "retailerMargin": "243.06",
            "roundOffGovt": "9.98",
            "specialPurposeFee": "234.71",
            "mrpPerUnit": "3960.00",
            "bottlesPerCase": "6",
            "mrpPerCase": "23760.00",
            "supplier": "Northern Spirits Limited",
            "warehouseCasesRaw": "0-1",
            "warehouseBottles": "1",
            "requestedCases": "0",
            "requestedBottles": "0"
        }

        normalized = service._normalize_item(raw_item)

        # Check key fields
        assert normalized["brand"] == "Aberfeldy Single Highland Malt Scotch Whisky Aged 12 Years"
        assert normalized["normalizedBrand"] == "aberfeldy single highland malt scotch whisky aged 12 years"
        assert normalized["measureMl"] == 750
        assert normalized["packageType"] == "Glass Bottle"
        assert normalized["bottlesPerCase"] == 6
        assert normalized["warehouseBottles"] == 1
        assert normalized["requestedCases"] == 0
        assert normalized["requestedBottles"] == 0

        # Check canonical key
        assert normalized["canonicalKey"] == "aberfeldy single highland malt scotch whisky aged 12 years|750|glass bottle"
        assert normalized["source"] == "WB_EXCISE_PREPARE_INDENT"

    def test_missing_optional_fields(self):
        """Test handling of missing optional fields"""
        from app.services.capture_service import CaptureService
        service = CaptureService()

        raw_item = {
            "brand": "Test Brand",
            "measureMl": "750",
            "packageType": "Glass Bottle",
            # Missing supplier and other optional fields
        }

        normalized = service._normalize_item(raw_item)

        assert normalized["brand"] == "Test Brand"
        assert normalized["supplier"] == ""
        assert normalized["strengthRaw"] == ""
        assert normalized["retailerMargin"] == "0"

class TestCaptureService:
    """Test capture service functionality"""

    def test_batch_creation(self):
        """Test capture batch creation"""
        from app.services.capture_service import CaptureService
        import tempfile
        import os

        # Create temporary captures directory
        temp_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(temp_dir, "captures"), exist_ok=True)

        # Mock the data directory
        original_cwd = os.getcwd()
        os.chdir(temp_dir)

        try:
            service = CaptureService()

            raw_batch = {
                "items": [
                    {
                        "brand": "Test Brand",
                        "measureMl": "750",
                        "packageType": "Glass Bottle"
                    }
                ]
            }

            batch_id = asyncio.run(service.save_capture(raw_batch))

            # Check that batch was saved
            assert re.match(r"\d{8}_\d{6}_[a-f0-9]{8}", batch_id)
            assert service.get_latest_capture() is not None
            assert len(service.get_all_captures()) == 1

            # Check file was created
            capture_file = os.path.join("data", "captures", f"{batch_id}.json")
            assert os.path.exists(capture_file)

        finally:
            os.chdir(original_cwd)
            # Clean up temp directory
            import shutil
            shutil.rmtree(temp_dir)

    def test_empty_batch_handling(self):
        """Test handling of empty batches"""
        from app.services.capture_service import CaptureService
        import tempfile
        import os
        import shutil

        temp_dir = tempfile.mkdtemp()
        original_cwd = os.getcwd()
        os.chdir(temp_dir)

        try:
            service = CaptureService()

            raw_batch = {
                "items": []  # Empty items
            }

            asyncio.run(service.save_capture(raw_batch))

            latest = service.get_latest_capture()
            assert latest is not None
            assert latest["itemCount"] == 0
            assert len(latest["items"]) == 0
        finally:
            os.chdir(original_cwd)
            shutil.rmtree(temp_dir)
