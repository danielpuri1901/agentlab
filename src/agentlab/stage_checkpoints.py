"""Save each finished stage of a paper video in S3, and reuse it on the next run.

A video run for one paper has three model stages: the deep read, the
storyboard, and the scene code. Before this module, a run that died in the
scene code had to pay for the deep read and the storyboard again on its next
try. Now each finished stage writes one JSON object under
checkpoints/<paper>/, and the next run for the same paper loads that object
instead of calling the model.

Each object key ends in a fingerprint of what made the stage: the model id
and the fingerprint of the stage before it. A different model, or a new
upstream output, gives a different key, so a stale stage never loads.
Raise CHECKPOINT_VERSION to retire every saved stage at once, for example
after a prompt change that makes old storyboards wrong.

A checkpoint is a cache, never a gate. A failed load is a miss, and a failed
save is a warning; neither one stops the video. The explain task role has no
s3:ListBucket, so S3 answers a missing key with 403 AccessDenied, not 404.
Both mean "not saved yet". A lifecycle rule deletes checkpoints after 30
days, so the prefix needs no object tags to stay clean.
"""

import hashlib
import json
import logging
import re

from botocore.exceptions import BotoCoreError, ClientError

CHECKPOINT_PREFIX = "checkpoints"
CHECKPOINT_VERSION = 1
PAPER_FOLDER_CHARS = 60

logger = logging.getLogger(__name__)


def fingerprint(*parts: str) -> str:
    """Twelve hex characters that change when any part changes."""
    text = json.dumps([CHECKPOINT_VERSION, *parts])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def paper_folder(identity: str) -> str:
    """A readable S3 folder name for a paper identity.

    An arXiv identity is unique as written, so its folder is the identity
    with the colon replaced: arxiv-2510.03215. A title identity can be long
    and can collide after the cut, so it also gets a short hash.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", identity).strip("-")
    if identity.startswith("arxiv:"):
        return slug
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:PAPER_FOLDER_CHARS].rstrip('-')}-{digest}"


class StageCheckpoints:
    """The saved stage outputs of one paper, in one S3 folder."""

    def __init__(self, s3_client, bucket: str, identity: str):
        self._s3 = s3_client
        self._bucket = bucket
        self.folder = f"{CHECKPOINT_PREFIX}/{paper_folder(identity)}"

    def key(self, stage: str, stage_fingerprint: str) -> str:
        return f"{self.folder}/{stage}-{stage_fingerprint}.json"

    def load(self, stage: str, stage_fingerprint: str) -> dict | None:
        """The saved output of a stage, or None when there is none to use."""
        key = self.key(stage, stage_fingerprint)
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
            data = json.loads(response["Body"].read())
        except (ClientError, BotoCoreError, ValueError) as exc:
            logger.info("checkpoint miss: %s (%s)", key, _reason(exc))
            return None
        if not isinstance(data, dict):
            logger.info("checkpoint miss: %s (not a JSON object)", key)
            return None
        logger.info("checkpoint hit: %s", key)
        return data

    def load_parsed(self, stage: str, stage_fingerprint: str, parse):
        """The saved output turned into a typed value by parse, or None.

        parse raises KeyError, TypeError, or ValueError (a pydantic
        ValidationError is a ValueError) when the saved shape no longer fits
        the code, for example after a schema change. That is a miss too.
        """
        data = self.load(stage, stage_fingerprint)
        if data is None:
            return None
        try:
            return parse(data)
        except (KeyError, TypeError, ValueError) as exc:
            key = self.key(stage, stage_fingerprint)
            logger.info("checkpoint miss: %s (unusable: %s)", key, type(exc).__name__)
            return None

    def save(self, stage: str, stage_fingerprint: str, data: dict) -> None:
        """Write a stage output. A failure only logs a warning."""
        key = self.key(stage, stage_fingerprint)
        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=json.dumps(data, indent=1).encode("utf-8"),
                ContentType="application/json",
            )
        except (ClientError, BotoCoreError) as exc:
            logger.warning("checkpoint save failed: %s (%s)", key, _reason(exc))
            return
        logger.info("checkpoint saved: %s", key)


class NoCheckpoints:
    """Stands in when a run has no S3 folder: every load misses, saves do nothing."""

    def load(self, stage: str, stage_fingerprint: str) -> None:
        return None

    def load_parsed(self, stage: str, stage_fingerprint: str, parse) -> None:
        return None

    def save(self, stage: str, stage_fingerprint: str, data: dict) -> None:
        return None


def _reason(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code", "ClientError")
    return type(exc).__name__
