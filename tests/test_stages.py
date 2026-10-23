import pytest

from agentlab import stages


@pytest.fixture(autouse=True)
def clear_stage_cache():
    """Each test reads the stage files fresh and leaves no cached stage behind."""
    stages.stage.cache_clear()
    yield
    stages.stage.cache_clear()


def test_four_stage_files_load_with_models():
    for name in ("deep_read", "storyboard", "scene", "reviewer"):
        spec = stages.stage(name)
        assert spec.name == name
        assert spec.model.startswith("anthropic/")
        assert spec.max_tokens > 0 and spec.timeout_seconds > 0 and spec.attempts >= 1
        assert len(spec.fingerprint) == 12


def test_fingerprint_changes_when_a_byte_changes(tmp_path, monkeypatch):
    (tmp_path / "scene.toml").write_text(
        'model = "anthropic/claude-opus-4-6"\nmax_tokens = 32000\ntimeout_seconds = 600\nattempts = 4\n'
    )
    monkeypatch.setattr(stages, "SPEC_DIR", tmp_path)
    stages.stage.cache_clear()
    before = stages.stage("scene").fingerprint
    (tmp_path / "scene.toml").write_text(
        'model = "anthropic/claude-opus-4-6"\nmax_tokens = 32000\ntimeout_seconds = 600\nattempts = 3\n'
    )
    stages.stage.cache_clear()
    assert stages.stage("scene").fingerprint != before
    assert stages.stage("scene").attempts == 3


def test_environment_overrides_the_file(monkeypatch):
    stages.stage.cache_clear()
    monkeypatch.setenv("SCENE_MODEL", "anthropic/claude-sonnet-4-6")
    assert stages.setting("scene", "model", "SCENE_MODEL") == "anthropic/claude-sonnet-4-6"
    monkeypatch.delenv("SCENE_MODEL")
    assert stages.setting("scene", "model", "SCENE_MODEL") == stages.stage("scene").model


def test_unknown_stage_raises():
    stages.stage.cache_clear()
    with pytest.raises(FileNotFoundError):
        stages.stage("nope")
