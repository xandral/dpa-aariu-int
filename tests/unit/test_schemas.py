"""Unit tests for Pydantic schema validation."""

import pytest
from pydantic import ValidationError

from app.schemas import UrlCreate, UrlUpdate


def test_url_create_valid():
    data = UrlCreate(url="https://example.com")
    # Pydantic AnyHttpUrl normalises URLs (may add trailing slash)
    assert "example.com" in str(data.url)


def test_url_create_invalid_url():
    with pytest.raises(ValidationError):
        UrlCreate(url="not-a-url")


def test_url_create_defaults():
    data = UrlCreate(url="https://example.com")
    assert data.frequency > 0
    assert 0.0 <= data.diff_threshold_ok <= 100.0
    assert 0.0 <= data.diff_threshold_alert <= 100.0
    assert 0.0 <= data.cosine_threshold_ok <= 1.0
    assert 0.0 <= data.cosine_threshold_alert <= 1.0


def test_url_create_custom_frequency():
    data = UrlCreate(url="https://example.com", frequency=300)
    assert data.frequency == 300


def test_url_create_frequency_must_be_positive():
    with pytest.raises(ValidationError):
        UrlCreate(url="https://example.com", frequency=0)

    with pytest.raises(ValidationError):
        UrlCreate(url="https://example.com", frequency=-1)


def test_url_create_threshold_out_of_range():
    with pytest.raises(ValidationError):
        UrlCreate(url="https://example.com", diff_threshold_ok=150.0)

    with pytest.raises(ValidationError):
        UrlCreate(url="https://example.com", cosine_threshold_ok=1.5)


def test_url_update_all_optional():
    # Empty update is valid
    data = UrlUpdate()
    assert data.frequency is None
    assert data.status is None


def test_url_update_partial():
    data = UrlUpdate(frequency=600)
    assert data.frequency == 600
    assert data.status is None


def test_url_update_invalid_frequency():
    with pytest.raises(ValidationError):
        UrlUpdate(frequency=-10)
