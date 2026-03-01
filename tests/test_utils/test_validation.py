import pytest

from homunculus.utils.validation import (
    VALID_TIMEZONES,
    validate_email,
    validate_phone,
    validate_timezone,
)


class TestValidatePhone:
    def test_valid_us_number(self):
        assert validate_phone("+14155551234") == "+14155551234"

    def test_valid_uk_number(self):
        assert validate_phone("+442071234567") == "+442071234567"

    def test_valid_short_number(self):
        assert validate_phone("+11") == "+11"

    def test_strips_whitespace(self):
        assert validate_phone("  +14155551234  ") == "+14155551234"

    def test_missing_plus(self):
        with pytest.raises(ValueError, match=r"E\.164"):
            validate_phone("14155551234")

    def test_letters(self):
        with pytest.raises(ValueError, match=r"E\.164"):
            validate_phone("+1415abc1234")

    def test_too_long(self):
        with pytest.raises(ValueError, match=r"E\.164"):
            validate_phone("+1234567890123456")

    def test_empty(self):
        with pytest.raises(ValueError, match=r"E\.164"):
            validate_phone("")

    def test_leading_zero_country_code(self):
        with pytest.raises(ValueError, match=r"E\.164"):
            validate_phone("+01234567890")


class TestValidateEmail:
    def test_valid_email(self):
        assert validate_email("user@example.com") == "user@example.com"

    def test_valid_subdomain(self):
        assert validate_email("user@mail.example.com") == "user@mail.example.com"

    def test_strips_whitespace(self):
        assert validate_email("  user@example.com  ") == "user@example.com"

    def test_missing_at(self):
        with pytest.raises(ValueError, match="email"):
            validate_email("userexample.com")

    def test_missing_domain(self):
        with pytest.raises(ValueError, match="email"):
            validate_email("user@")

    def test_missing_tld(self):
        with pytest.raises(ValueError, match="email"):
            validate_email("user@example")

    def test_empty(self):
        with pytest.raises(ValueError, match="email"):
            validate_email("")


class TestValidateTimezone:
    def test_valid_timezone(self):
        assert validate_timezone("America/New_York") == "America/New_York"

    def test_valid_utc(self):
        assert validate_timezone("UTC") == "UTC"

    def test_strips_whitespace(self):
        assert validate_timezone("  Europe/London  ") == "Europe/London"

    def test_invalid_timezone(self):
        with pytest.raises(ValueError, match="timezone"):
            validate_timezone("Not/A/Timezone")

    def test_empty(self):
        with pytest.raises(ValueError, match="timezone"):
            validate_timezone("")

    def test_partial_match_not_accepted(self):
        with pytest.raises(ValueError, match="timezone"):
            validate_timezone("America")


class TestValidTimezones:
    def test_contains_common_timezones(self):
        assert "America/New_York" in VALID_TIMEZONES
        assert "Europe/London" in VALID_TIMEZONES
        assert "Asia/Tokyo" in VALID_TIMEZONES
        assert "UTC" in VALID_TIMEZONES

    def test_is_frozenset(self):
        assert isinstance(VALID_TIMEZONES, frozenset)
