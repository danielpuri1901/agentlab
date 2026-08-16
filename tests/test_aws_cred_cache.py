"""Offline tests for the credential cache freshness logic."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "aws_cred_cache", Path(__file__).parent.parent / "scripts" / "aws_cred_cache.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

NOW = datetime(2026, 8, 16, 15, 0, tzinfo=UTC)


def _raw(minutes_from_now):
    return json.dumps(
        {"Version": 1, "AccessKeyId": "x", "SecretAccessKey": "y",
         "SessionToken": "z",
         "Expiration": (NOW + timedelta(minutes=minutes_from_now)).isoformat()}
    )


def test_fresh_when_more_than_five_minutes_left():
    assert mod.cache_is_fresh(_raw(6), NOW)


def test_stale_at_five_minutes_or_less():
    assert not mod.cache_is_fresh(_raw(5), NOW)
    assert not mod.cache_is_fresh(_raw(-1), NOW)


def test_stale_on_garbage_or_missing_fields():
    assert not mod.cache_is_fresh("", NOW)
    assert not mod.cache_is_fresh("{not json", NOW)
    assert not mod.cache_is_fresh(json.dumps({"Version": 1}), NOW)
