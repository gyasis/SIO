"""T071 [US7] Unit tests for Claude Code installer."""

from __future__ import annotations

import json

from sio.adapters.claude_code.installer import _install_config, install


class TestInstaller:
    """Claude Code installer creates required structure."""

    def test_creates_db_directory(self, tmp_path):
        db_dir = tmp_path / ".sio" / "claude-code"
        install(
            db_dir=str(db_dir),
            claude_dir=str(tmp_path / ".claude"),
            dry_run=True,
        )
        assert db_dir.exists()

    def test_initializes_database(self, tmp_path):
        db_dir = tmp_path / ".sio" / "claude-code"
        result = install(
            db_dir=str(db_dir),
            claude_dir=str(tmp_path / ".claude"),
            dry_run=True,
        )
        db_path = db_dir / "behavior_invocations.db"
        assert db_path.exists()
        assert result["db_created"] is True

    def test_registers_hooks_in_settings(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        settings = claude_dir / "settings.json"
        settings.write_text(json.dumps({"hooks": {}}))

        result = install(
            db_dir=str(tmp_path / ".sio" / "claude-code"),
            claude_dir=str(claude_dir),
            dry_run=True,
        )
        assert result["hooks_registered"] is True

    def test_merges_with_existing_hooks(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        settings = claude_dir / "settings.json"
        existing = {
            "hooks": {
                "PostToolUse": [
                    {"type": "command", "command": "existing-hook"}
                ]
            }
        }
        settings.write_text(json.dumps(existing))

        install(
            db_dir=str(tmp_path / ".sio" / "claude-code"),
            claude_dir=str(claude_dir),
            dry_run=True,
        )
        data = json.loads(settings.read_text())
        post_hooks = data["hooks"]["PostToolUse"]
        # Should have both existing and SIO hooks
        assert len(post_hooks) >= 2

    def test_creates_platform_config_record(self, tmp_path):
        db_dir = tmp_path / ".sio" / "claude-code"
        result = install(
            db_dir=str(db_dir),
            claude_dir=str(tmp_path / ".claude"),
            dry_run=True,
        )
        assert result["platform_config_saved"] is True

    def test_returns_summary(self, tmp_path):
        result = install(
            db_dir=str(tmp_path / ".sio" / "claude-code"),
            claude_dir=str(tmp_path / ".claude"),
            dry_run=True,
        )
        assert "db_created" in result
        assert "hooks_registered" in result
        assert "platform" in result
        assert result["platform"] == "claude-code"


class TestInstallerConfig:
    """T031: install() creates config.toml with [llm] section template."""

    def test_install_creates_config_toml(self, tmp_path, monkeypatch):
        """sio install creates ~/.sio/config.toml with [llm] section."""
        sio_base = tmp_path / ".sio"
        monkeypatch.setenv("HOME", str(tmp_path))
        result = install(
            db_dir=str(sio_base / "claude-code"),
            claude_dir=str(tmp_path / ".claude"),
        )
        config_path = sio_base / "config.toml"
        assert config_path.exists(), "config.toml was not created"
        content = config_path.read_text()
        assert "[llm]" in content
        assert "temperature" in content
        assert "azure" in content.lower()
        assert "anthropic" in content.lower()
        assert "openai" in content.lower()
        assert "ollama" in content.lower()
        assert result["config_created"] is True

    def test_install_does_not_overwrite_existing_config(self, tmp_path, monkeypatch):
        """Existing config.toml is preserved — never overwritten."""
        sio_base = tmp_path / ".sio"
        sio_base.mkdir(parents=True)
        config_path = sio_base / "config.toml"
        user_content = '[llm]\nmodel = "openai/gpt-4o"\n'
        config_path.write_text(user_content)

        monkeypatch.setenv("HOME", str(tmp_path))
        result = install(
            db_dir=str(sio_base / "claude-code"),
            claude_dir=str(tmp_path / ".claude"),
        )
        assert config_path.read_text() == user_content
        assert result["config_created"] is False

    def test_install_config_standalone(self, tmp_path):
        """_install_config() creates the template independently."""
        sio_base = str(tmp_path / ".sio")
        created = _install_config(sio_base)
        assert created is True
        config_path = tmp_path / ".sio" / "config.toml"
        assert config_path.exists()
        content = config_path.read_text()
        assert "[llm]" in content
        assert "[llm.sub]" in content

    def test_install_config_idempotent(self, tmp_path):
        """_install_config() returns False if file already exists."""
        sio_base = str(tmp_path / ".sio")
        _install_config(sio_base)
        created_again = _install_config(sio_base)
        assert created_again is False
