"""Stage checkpoints: one JSON object per finished stage, keyed by what made it."""

import logging

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from agentlab.stage_checkpoints import (
    NoCheckpoints,
    StageCheckpoints,
    fingerprint,
    paper_folder,
)

BUCKET = "agentlab-results-test"
REGION = "us-east-1"


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        yield client


def test_a_saved_stage_loads_back(s3):
    checkpoints = StageCheckpoints(s3, BUCKET, "arxiv:2510.03215")
    stage_fingerprint = fingerprint(
        "deep_read", "sonnet", "https://arxiv.org/abs/2510.03215"
    )
    checkpoints.save(
        "deep_read", stage_fingerprint, {"digest": "# Digest", "plan": {"a": 1}}
    )

    loaded = checkpoints.load("deep_read", stage_fingerprint)

    assert loaded == {"digest": "# Digest", "plan": {"a": 1}}
    key = f"checkpoints/arxiv-2510.03215/deep_read-{stage_fingerprint}.json"
    assert s3.get_object(Bucket=BUCKET, Key=key)["ContentType"] == "application/json"


def test_a_stage_never_saved_is_a_miss(s3):
    checkpoints = StageCheckpoints(s3, BUCKET, "arxiv:2510.03215")

    assert checkpoints.load("storyboard", fingerprint("storyboard", "x")) is None


def test_a_new_model_does_not_load_the_old_stage(s3):
    checkpoints = StageCheckpoints(s3, BUCKET, "arxiv:2510.03215")
    old = fingerprint("storyboard", "sonnet-4-6", "plan-a")
    checkpoints.save("storyboard", old, {"title": "old"})

    assert (
        checkpoints.load("storyboard", fingerprint("storyboard", "opus-5-5", "plan-a"))
        is None
    )
    assert (
        checkpoints.load(
            "storyboard", fingerprint("storyboard", "sonnet-4-6", "plan-b")
        )
        is None
    )
    assert checkpoints.load("storyboard", old) == {"title": "old"}


def test_a_later_save_replaces_the_stage(s3):
    checkpoints = StageCheckpoints(s3, BUCKET, "arxiv:2510.03215")
    stage_fingerprint = fingerprint("scene", "m")
    checkpoints.save(
        "scene", stage_fingerprint, {"attempt": 1, "status": "render_error"}
    )
    checkpoints.save("scene", stage_fingerprint, {"attempt": 2, "status": "written"})

    assert checkpoints.load("scene", stage_fingerprint) == {
        "attempt": 2,
        "status": "written",
    }


def test_access_denied_is_a_miss_not_an_error(caplog):
    class DeniedS3:
        def get_object(self, **_kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    checkpoints = StageCheckpoints(DeniedS3(), BUCKET, "arxiv:2510.03215")
    with caplog.at_level(logging.INFO, logger="agentlab.stage_checkpoints"):
        assert checkpoints.load("deep_read", "abc") is None
    assert "checkpoint miss" in caplog.text
    assert "AccessDenied" in caplog.text


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"])
def test_an_unreadable_object_is_a_miss(s3, body):
    checkpoints = StageCheckpoints(s3, BUCKET, "arxiv:2510.03215")
    s3.put_object(Bucket=BUCKET, Key=checkpoints.key("deep_read", "abc"), Body=body)

    assert checkpoints.load("deep_read", "abc") is None


def test_a_failed_save_only_warns(caplog):
    class BrokenS3:
        def put_object(self, **_kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")

    checkpoints = StageCheckpoints(BrokenS3(), BUCKET, "arxiv:2510.03215")
    with caplog.at_level(logging.WARNING, logger="agentlab.stage_checkpoints"):
        checkpoints.save("deep_read", "abc", {"digest": "x"})
    assert "checkpoint save failed" in caplog.text


def test_fingerprint_is_stable_and_order_sensitive():
    assert fingerprint("a", "b") == fingerprint("a", "b")
    assert fingerprint("a", "b") != fingerprint("b", "a")
    assert len(fingerprint("a")) == 12


def test_paper_folders_are_readable_and_safe():
    assert paper_folder("arxiv:2510.03215") == "arxiv-2510.03215"
    title_folder = paper_folder("title:attention is all you need")
    assert title_folder.startswith("title-attention-is-all-you-need-")
    long_folder = paper_folder("title:" + "very long title " * 20)
    assert len(long_folder) <= 60 + 9
    assert paper_folder("title:a/b c") != paper_folder("title:a b/c")


def test_load_parsed_returns_the_typed_value(s3):
    checkpoints = StageCheckpoints(s3, BUCKET, "arxiv:2510.03215")
    checkpoints.save("deep_read", "abc", {"digest": "# Digest"})

    assert (
        checkpoints.load_parsed("deep_read", "abc", lambda d: d["digest"]) == "# Digest"
    )


def test_a_saved_shape_the_code_no_longer_reads_is_a_miss(s3):
    checkpoints = StageCheckpoints(s3, BUCKET, "arxiv:2510.03215")
    checkpoints.save("deep_read", "abc", {"old_field": 1})

    def parse(data):
        return data["digest"]

    assert checkpoints.load_parsed("deep_read", "abc", parse) is None


def test_no_checkpoints_always_misses_and_saves_nothing():
    checkpoints = NoCheckpoints()
    checkpoints.save("deep_read", "abc", {"digest": "x"})

    assert checkpoints.load("deep_read", "abc") is None
    assert checkpoints.load_parsed("deep_read", "abc", dict) is None
