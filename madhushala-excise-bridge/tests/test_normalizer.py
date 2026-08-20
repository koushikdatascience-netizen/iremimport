"""
Tests for the normalizer module
"""
import pytest
from app.services.normalizer import normalize_brand, normalize_package_type

def test_normalize_brand():
    """Test brand name normalization"""
    assert normalize_brand("BENGAL TIGER PREMIUM ") == "bengal tiger premium"
    assert normalize_brand("Bengal Tiger Premium") == "bengal tiger premium"
    assert normalize_brand("Bengal Tiger's Premium") == "bengal tigers premium"

def test_normalize_package_type():
    """Test package type normalization"""
    assert normalize_package_type("Pet Bottle") == "pet bottle"
    assert normalize_package_type("PET BOTTLE ") == "pet bottle"
    assert normalize_package_type("Glass Bottle") == "glass bottle"

if __name__ == "__main__":
    pytest.main([__file__])
</arg_value>
<task_progress>
- [x] Create main application file
- [x] Implement Playwright automation for manual consent
- [x] Create static HTML demo page
- [x] Implement Madhushala API client
- [x] Create configuration file
- [x] Create database models
- [x] Create database connection module
- [x] Initialize git repository
- [x] Create requirements.txt
- [x] Create .env.example
- [x] Create README.md
- [x] Create style.css
- [x] Create app.js
- [x] Create test files
- [ ] Run syntax checks
- [ ] Run pytest
- [ ] Verify server starts
- [ ] Verify /health endpoint
- [ ] Verify demo page loads
- [ ] Verify Playwright launches
- [ ] Create IMPLEMENTATION_STATUS.md
</task_progress>
</write_to_file>