import pytest
from unittest.mock import patch, MagicMock
from api.embeddings import OllamaEmbeddingClient, ChromaDBClient, SemanticSearchEngine, AnomalyDetector, RAGChatEngine


def test_ollama_embedding_client_init():
    """Test OllamaEmbeddingClient initialization"""
    client = OllamaEmbeddingClient()
    assert client.base_url == "http://localhost:11434"
    assert client.model == "nomic-embed-text"
    
    # Test with custom parameters
    client = OllamaEmbeddingClient(base_url="http://test:1234", model="test-model")
    assert client.base_url == "http://test:1234"
    assert client.model == "test-model"


@patch('api.embeddings.requests.Session.post')
def test_ollama_embedding_client_get_embedding(mock_post):
    """Test getting embedding from Ollama"""
    # Mock successful response
    mock_response = MagicMock()
    mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response
    
    client = OllamaEmbeddingClient()
    result = client.get_embedding("test text")
    
    # Check result
    assert result == [0.1, 0.2, 0.3]
    
    # Check request was made correctly
    mock_post.assert_called_once_with(
        "http://localhost:11434/api/embeddings",
        json={"model": "nomic-embed-text", "prompt": "test text"},
        timeout=(10, 30)
    )


@patch('api.embeddings.requests.Session.post')
def test_ollama_embedding_client_get_embedding_error(mock_post):
    """Test handling of errors in embedding client"""
    # Mock error response
    mock_post.side_effect = Exception("Connection error")
    
    client = OllamaEmbeddingClient()
    result = client.get_embedding("test text")
    
    # Should return None on error
    assert result is None


def test_chromadb_client_init():
    """Test ChromaDBClient initialization"""
    client = ChromaDBClient()
    assert client.persist_directory == "/var/lib/chimera/chromadb"
    assert client.collection_name == "log_embeddings"


@patch('api.embeddings.ChromaDBClient')
@patch('api.embeddings.OllamaEmbeddingClient')
def test_semantic_search_engine_init(mock_ollama, mock_chroma):
    """Test SemanticSearchEngine initialization"""
    # Mock the clients
    mock_ollama_instance = MagicMock()
    mock_ollama.return_value = mock_ollama_instance
    
    mock_chroma_instance = MagicMock()
    mock_chroma.return_value = mock_chroma_instance
    
    engine = SemanticSearchEngine()
    
    # Check components are initialized
    assert engine.embedding_client == mock_ollama_instance
    assert engine.chroma_client == mock_chroma_instance


@patch('api.embeddings.requests.Session.post')
def test_rag_chat_engine_init(mock_post):
    """Test RAGChatEngine initialization"""
    engine = RAGChatEngine()
    
    # Check components are initialized
    assert engine.ollama_url == "http://localhost:11434"
    assert engine.ollama_model == "qwen3:1.7b"
    assert isinstance(engine.search_engine, SemanticSearchEngine)


def test_anomaly_detector_init():
    """Test AnomalyDetector initialization"""
    detector = AnomalyDetector()
    assert detector.db_path is None
    
    # Test with db_path
    detector = AnomalyDetector("/tmp/test.db")
    assert detector.db_path == "/tmp/test.db"