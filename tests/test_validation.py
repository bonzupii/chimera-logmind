import pytest
from api.server import validate_integer_param, validate_string_param, validate_path_param


def test_validate_integer_param():
    """Test integer parameter validation"""
    # Valid cases
    assert validate_integer_param("10", "test") == 10
    assert validate_integer_param("0", "test", min_val=0) == 0
    assert validate_integer_param("100", "test", max_val=1000) == 100
    assert validate_integer_param("50", "test", min_val=0, max_val=100) == 50
    
    # Invalid cases
    with pytest.raises(ValueError, match="must be >= 0"):
        validate_integer_param("-1", "test", min_val=0)
        
    with pytest.raises(ValueError, match="must be <= 100"):
        validate_integer_param("101", "test", max_val=100)
        
    with pytest.raises(ValueError, match="Invalid test"):
        validate_integer_param("abc", "test")


def test_validate_string_param():
    """Test string parameter validation"""
    # Valid cases
    assert validate_string_param("test", "test") == "test"
    assert validate_string_param("  test  ", "test") == "test"  # Stripped
    assert validate_string_param("a" * 100, "test", max_length=100) == "a" * 100
    
    # With allowed characters
    assert validate_string_param("abc123", "test", max_length=10, allowed_chars="abcdefghijklmnopqrstuvwxyz0123456789") == "abc123"
    
    # Invalid cases
    with pytest.raises(ValueError, match="exceeds maximum length"):
        validate_string_param("a" * 101, "test", max_length=100)
        
    with pytest.raises(ValueError, match="contains invalid characters"):
        validate_string_param("abc!", "test", max_length=10, allowed_chars="abcdefghijklmnopqrstuvwxyz")


def test_validate_path_param():
    """Test path parameter validation"""
    # Valid cases
    assert validate_path_param("test.log", "test") == "test.log"
    assert validate_path_param("logs/test.log", "test") == "logs/test.log"
    
    # Invalid cases - path traversal
    with pytest.raises(ValueError, match="path traversal not allowed"):
        validate_path_param("../test.log", "test")
        
    with pytest.raises(ValueError, match="path traversal not allowed"):
        validate_path_param("/etc/passwd", "test")