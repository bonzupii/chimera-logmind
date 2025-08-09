from pathlib import Path


def test_service_file_no_duplicates():
    content = Path('ops/chimera-api.service').read_text()
    # Only one WorkingDirectory line
    assert content.count('WorkingDirectory=') == 1
    assert 'Environment=CHIMERA_LOG_LEVEL=' in content
