import json
import os
from api.config import ChimeraConfig, LogSource


def test_config_default():
    """Test default configuration creation"""
    config = ChimeraConfig.default()
    
    # Check basic properties
    assert config.db_path is not None
    assert config.socket_path is not None
    assert len(config.log_sources) > 0
    
    # Check default sources
    source_names = [source.name for source in config.log_sources]
    assert "system-journald" in source_names
    assert "system-files" in source_names
    assert "docker-containers" in source_names


def test_config_to_dict():
    """Test conversion to dictionary"""
    config = ChimeraConfig.default()
    config_dict = config.to_dict()
    
    # Check all expected keys are present
    expected_keys = {"log_sources", "db_path", "socket_path", "max_ingest_limit", "default_retention_days"}
    assert set(config_dict.keys()) == expected_keys
    
    # Check log sources structure
    assert isinstance(config_dict["log_sources"], list)
    assert len(config_dict["log_sources"]) > 0


def test_config_from_dict():
    """Test creation from dictionary"""
    config_data = {
        "log_sources": [
            {
                "name": "test-source",
                "type": "journald",
                "enabled": True,
                "config": {"units": ["sshd"]}
            }
        ],
        "db_path": "/tmp/test.db",
        "socket_path": "/tmp/test.sock",
        "max_ingest_limit": 5000,
        "default_retention_days": 15
    }
    
    config = ChimeraConfig.from_dict(config_data)
    
    # Check properties are set correctly
    assert config.db_path == "/tmp/test.db"
    assert config.socket_path == "/tmp/test.sock"
    assert config.max_ingest_limit == 5000
    assert config.default_retention_days == 15
    
    # Check log source
    assert len(config.log_sources) == 1
    source = config.log_sources[0]
    assert source.name == "test-source"
    assert source.type == "journald"
    assert source.enabled is True
    assert source.config == {"units": ["sshd"]}


def test_config_source_management():
    """Test adding, removing, and updating sources"""
    config = ChimeraConfig.default()
    initial_count = len(config.log_sources)
    
    # Test adding a source
    new_source = LogSource(
        name="test-source",
        type="file",
        enabled=True,
        config={"paths": ["/var/log/test.log"]}
    )
    
    config.add_source(new_source)
    assert len(config.log_sources) == initial_count + 1
    
    # Test getting a source by name
    retrieved = config.get_source_by_name("test-source")
    assert retrieved is not None
    assert retrieved.name == "test-source"
    
    # Test updating a source
    success = config.update_source("test-source", enabled=False)
    assert success is True
    updated = config.get_source_by_name("test-source")
    assert updated.enabled is False
    
    # Test removing a source
    success = config.remove_source("test-source")
    assert success is True
    assert len(config.log_sources) == initial_count
    assert config.get_source_by_name("test-source") is None


def test_config_get_enabled_sources():
    """Test filtering enabled sources"""
    config = ChimeraConfig.default()
    
    # Add a disabled source
    disabled_source = LogSource(
        name="disabled-source",
        type="file",
        enabled=False,
        config={"paths": ["/var/log/test.log"]}
    )
    config.add_source(disabled_source)
    
    # Get enabled sources
    enabled_sources = config.get_enabled_sources()
    
    # All default sources should be enabled except the one we just added
    default_enabled_count = len([s for s in config.log_sources if s.enabled and s.name != "disabled-source"])
    assert len(enabled_sources) == default_enabled_count


def test_config_save_load(tmp_path, monkeypatch):
    """Test saving and loading configuration from file"""
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("CHIMERA_CONFIG_PATH", str(config_path))
    
    # Create a config and save it
    config = ChimeraConfig(
        log_sources=[
            LogSource(
                name="test-source",
                type="journald",
                enabled=True,
                config={"units": ["sshd"]}
            )
        ],
        db_path="/tmp/test.db",
        socket_path="/tmp/test.sock"
    )
    
    config.save(str(config_path))
    
    # Check file was created
    assert config_path.exists()
    
    # Load config from file
    loaded_config = ChimeraConfig.load(str(config_path))
    
    # Check properties match
    assert loaded_config.db_path == config.db_path
    assert loaded_config.socket_path == config.socket_path
    assert len(loaded_config.log_sources) == len(config.log_sources)
    
    # Check source properties
    original_source = config.log_sources[0]
    loaded_source = loaded_config.log_sources[0]
    assert loaded_source.name == original_source.name
    assert loaded_source.type == original_source.type
    assert loaded_source.enabled == original_source.enabled
    assert loaded_source.config == original_source.config