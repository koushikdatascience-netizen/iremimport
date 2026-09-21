from app.excise_portals import resolve_excise_portal


def test_registry_resolves_alias_and_generic_profile():
    registry = {
        "JHARKHAND": {
            "aliases": ["JH", "JHARKHAND"],
            "loginUrl": "https://excise.example.gov.in/login",
            "allowedOrigins": ["https://excise.example.gov.in"],
            "usernameSelectors": ["#user"],
            "passwordSelectors": ["#password"],
            "captchaSelectors": ["#captcha"],
            "loginSelectors": ["#login"],
            "loginText": ["login"],
            "autoSubmit": False,
        }
    }

    portal = resolve_excise_portal("jh", registry=registry)

    assert portal is not None
    assert portal["state"] == "JHARKHAND"
    assert portal["loginUrl"] == "https://excise.example.gov.in/login"
    assert portal["loginProfile"]["allowedOrigins"] == ["https://excise.example.gov.in"]
    assert portal["loginProfile"]["usernameSelectors"] == ["#user"]
    assert portal["loginProfile"]["passwordSelectors"] == ["#password"]
    assert portal["loginProfile"]["captchaSelectors"] == ["#captcha"]
    assert portal["loginProfile"]["loginSelectors"] == ["#login"]
    assert portal["loginProfile"]["autoSubmit"] is False


def test_registry_unknown_state_is_not_silently_redirected():
    registry = {
        "WEST BENGAL": {
            "aliases": ["WB"],
            "loginUrl": "https://excise.wb.gov.in/login",
        }
    }

    assert resolve_excise_portal("ODISHA", registry=registry) is None
