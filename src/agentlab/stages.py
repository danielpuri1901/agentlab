"""Stage settings, one TOML file per model stage (docs/superpowers/specs/
2026-10-23-chapter-lanes-design.md, section 8).

A stage file holds the model, its effort or thinking setting and the budgets.
Prompts stay in Python. The environment overrides a field so Terraform can
swap a model without an image build; `setting()` applies that precedence.
The fingerprint of the file bytes goes into checkpoint fingerprints, so a
settings change invalidates a saved stage the way a prompt change does.
"""
import hashlib
import os
import tomllib
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field

SPEC_DIR = Path(__file__).with_name("stage_specs")


class Stage(BaseModel):
    name: str
    model: str = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    attempts: int = Field(ge=1)
    effort: str | None = None
    thinking: str | None = None
    fingerprint: str = Field(min_length=12, max_length=12)


@cache
def stage(name: str) -> Stage:
    """The settings of one stage, read once per process."""
    raw = (SPEC_DIR / f"{name}.toml").read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    return Stage(name=name, fingerprint=hashlib.sha256(raw).hexdigest()[:12], **data)


def setting(name: str, field: str, env: str):
    """The environment variable when it is set, else the stage file's value."""
    value = os.environ.get(env)
    if value:
        return value
    return getattr(stage(name), field)
