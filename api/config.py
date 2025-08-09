#!/usr/bin/env python3
import json
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class LogSource:
    """Configuration for a log ingestion source"""
    name: str
    type: str  # journald, file, container, ssh, network
    enabled: bool = True
    config: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.config is None:
            self.config = {}


@dataclass
class ChimeraConfig:
    """Main configuration for Chimera LogMind"""
    log_sources: List[LogSource]
    db_path: str
    socket_path: str
    max_ingest_limit: int = 10000
    default_retention_days: int = 30
    
    @classmethod
    def load(cls, config_path: Optional[str] = None) -> 'ChimeraConfig':
        """Load configuration from file or create default"""
        if config_path is None:
            config_path = os.environ.get('CHIMERA_CONFIG_PATH', '/etc/chimera/config.json')
        
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                data = json.load(f)
                return cls.from_dict(data)
        else:
            return cls.default()
    
    def save(self, config_path: Optional[str] = None) -> None:
        """Save configuration to file"""
        if config_path is None:
            config_path = os.environ.get('CHIMERA_CONFIG_PATH', '/etc/chimera/config.json')
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        
        with open(config_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'log_sources': [asdict(source) for source in self.log_sources],
            'db_path': self.db_path,
            'socket_path': self.socket_path,
            'max_ingest_limit': self.max_ingest_limit,
            'default_retention_days': self.default_retention_days,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ChimeraConfig':
        """Create from dictionary"""
        sources = [LogSource(**source_data) for source_data in data.get('log_sources', [])]
        return cls(
            log_sources=sources,
            db_path=data.get('db_path', '/var/lib/chimera/chimera.duckdb'),
            socket_path=data.get('socket_path', '/run/chimera/api.sock'),
            max_ingest_limit=data.get('max_ingest_limit', 10000),
            default_retention_days=data.get('default_retention_days', 30),
        )
    
    @classmethod
    def default(cls) -> 'ChimeraConfig':
        """Create default configuration"""
        return cls(
            log_sources=[
                LogSource(
                    name='system-journald',
                    type='journald',
                    enabled=True,
                    config={
                        'units': [],  # Empty means all units
                        'exclude_units': ['systemd-*', 'dbus-*'],
                        'priority_min': 'notice',
                    }
                ),
                LogSource(
                    name='system-files',
                    type='file',
                    enabled=True,
                    config={
                        'paths': [
                            '/var/log/syslog',
                            '/var/log/auth.log',
                            '/var/log/kern.log',
                            '/var/log/dpkg.log',
                        ],
                        'patterns': ['*.log', '*.log.*'],
                        'max_file_size_mb': 100,
                    }
                ),
                LogSource(
                    name='docker-containers',
                    type='container',
                    enabled=False,  # Disabled by default
                    config={
                        'runtime': 'docker',
                        'include_patterns': ['*'],
                        'exclude_patterns': ['chimera-*'],
                    }
                ),
            ],
            db_path=os.environ.get('CHIMERA_DB_PATH', '/var/lib/chimera/chimera.duckdb'),
            socket_path=os.environ.get('CHIMERA_API_SOCKET', '/run/chimera/api.sock'),
        )
    
    def get_enabled_sources(self) -> List[LogSource]:
        """Get list of enabled log sources"""
        return [source for source in self.log_sources if source.enabled]
    
    def get_source_by_name(self, name: str) -> Optional[LogSource]:
        """Get a specific log source by name"""
        for source in self.log_sources:
            if source.name == name:
                return source
        return None
    
    def add_source(self, source: LogSource) -> None:
        """Add a new log source"""
        # Check for name conflicts
        if self.get_source_by_name(source.name):
            raise ValueError(f"Log source '{source.name}' already exists")
        self.log_sources.append(source)
    
    def remove_source(self, name: str) -> bool:
        """Remove a log source by name"""
        for i, source in enumerate(self.log_sources):
            if source.name == name:
                del self.log_sources[i]
                return True
        return False
    
    def update_source(self, name: str, **kwargs) -> bool:
        """Update an existing log source"""
        source = self.get_source_by_name(name)
        if not source:
            return False
        
        for key, value in kwargs.items():
            if hasattr(source, key):
                setattr(source, key, value)
        return True