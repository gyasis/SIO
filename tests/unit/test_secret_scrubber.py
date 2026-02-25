"""Tests for sio.core.telemetry.secret_scrubber."""

from __future__ import annotations

import pytest

from sio.core.telemetry.secret_scrubber import scrub


class TestScrubAwsAccessKey:
    def test_standalone_key(self):
        result = scrub("aws_access_key_id=AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED]" in result

    def test_aws_secret_key(self):
        result = scrub("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in result
        assert "[REDACTED]" in result


class TestScrubBearerToken:
    def test_jwt_bearer(self):
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = scrub(f"Authorization: Bearer {token}")
        assert token not in result
        assert "[REDACTED]" in result


class TestScrubApiKey:
    def test_api_key_equals(self):
        result = scrub("api_key=sk-abc123xyz456def789")
        assert "sk-abc123xyz456def789" not in result
        assert "[REDACTED]" in result

    def test_x_api_key_header(self):
        result = scrub("x-api-key: live_token_abcdef1234567890")
        assert "live_token_abcdef1234567890" not in result
        assert "[REDACTED]" in result


class TestScrubPassword:
    def test_password_equals(self):
        result = scrub("password=mysecret123")
        assert "mysecret123" not in result
        assert "[REDACTED]" in result

    def test_passwd_equals(self):
        result = scrub("passwd=hunter2")
        assert "hunter2" not in result

    def test_password_in_json(self):
        result = scrub('{"password": "my_db_password_123"}')
        assert "my_db_password_123" not in result


class TestScrubConnectionString:
    def test_postgresql(self):
        result = scrub("postgresql://admin:secretpass@db.example.com:5432/mydb")
        assert "secretpass" not in result
        assert "[REDACTED]" in result

    def test_mysql(self):
        result = scrub("mysql://root:p@ssw0rd@localhost/app")
        assert "[REDACTED]" in result

    def test_mongodb(self):
        result = scrub("mongodb://dbuser:dbpass123@cluster0.mongodb.net/test")
        assert "dbpass123" not in result


class TestScrubMultipleSecrets:
    def test_mixed_secrets(self):
        text = (
            "aws_access_key_id=AKIAIOSFODNN7EXAMPLE\n"
            "password=supersecret\n"
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig\n"
        )
        result = scrub(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "supersecret" not in result
        assert "eyJhbGciOiJIUzI1NiJ9" not in result


class TestNoFalsePositiveNormalText:
    def test_plain_english(self):
        text = "The quick brown fox jumps over the lazy dog."
        assert scrub(text) == text

    def test_code_snippet(self):
        text = "def hello():\n    return 'world'\n"
        assert scrub(text) == text

    def test_url_without_credentials(self):
        text = "Visit https://example.com/docs for more info."
        assert scrub(text) == text


class TestNoFalsePositiveSimilarPatterns:
    def test_password_in_prose(self):
        text = "Make sure your password is at least 12 characters long."
        assert scrub(text) == text

    def test_word_api_in_docs(self):
        text = "The API supports pagination and filtering."
        assert scrub(text) == text


class TestEmptyString:
    def test_empty(self):
        assert scrub("") == ""


class TestNoneLikeInput:
    def test_none_string(self):
        assert scrub("None") == "None"

    def test_null_string(self):
        assert scrub("null") == "null"


class TestScrubGenericSecrets:
    def test_secret_key(self):
        result = scrub("SECRET_KEY=django-insecure-abc123def456")
        assert "django-insecure-abc123def456" not in result
        assert "[REDACTED]" in result
