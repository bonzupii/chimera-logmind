import json
import logging
import re
import time
from typing import Dict, List, Optional, Tuple
import requests
from dataclasses import dataclass

from .embeddings import SemanticSearchEngine
from .db import Database

logger = logging.getLogger(__name__)

@dataclass
class ChatMessage:
    role: str  # "user" or "assistant"
    content: str
    timestamp: float

@dataclass
class ChatResponse:
    response: str
    sources: List[Dict]
    confidence: float
    query_time: float

class RAGChatEngine:
    """Retrieval-Augmented Generation chat engine for log analysis."""
    
    def __init__(self, db: Database, search_engine: SemanticSearchEngine, ollama_url: str = "http://localhost:11434"):
        self.db = db
        self.search_engine = search_engine
        self.ollama_url = ollama_url
        self.conversation_history: List[ChatMessage] = []
        self.max_history = 10
        
    def add_message(self, role: str, content: str):
        """Add a message to the conversation history."""
        self.conversation_history.append(ChatMessage(
            role=role,
            content=content,
            timestamp=time.time()
        ))
        
        # Keep only recent history
        if len(self.conversation_history) > self.max_history:
            self.conversation_history = self.conversation_history[-self.max_history:]
    
    def _extract_search_terms(self, query: str) -> List[str]:
        """Extract potential search terms from a user query."""
        # Simple keyword extraction - could be enhanced with NLP
        keywords = []
        
        # Common log analysis terms
        log_terms = ['error', 'warning', 'critical', 'failed', 'failure', 'exception', 
                    'timeout', 'connection', 'service', 'process', 'memory', 'cpu', 
                    'disk', 'network', 'security', 'authentication', 'authorization']
        
        # Extract terms that might be in logs
        for term in log_terms:
            if term.lower() in query.lower():
                keywords.append(term)
        
        # Extract quoted strings as exact search terms
        quoted = re.findall(r'"([^"]*)"', query)
        keywords.extend(quoted)
        
        # Extract systemd units, services, etc.
        service_patterns = [
            r'\b(sshd|nginx|apache|mysql|postgres|docker|kubelet|systemd)\b',
            r'\b([a-zA-Z0-9-]+\.service)\b',
            r'\b([a-zA-Z0-9-]+\.socket)\b'
        ]
        
        for pattern in service_patterns:
            matches = re.findall(pattern, query, re.IGNORECASE)
            keywords.extend(matches)
        
        return list(set(keywords))
    
    def _search_relevant_logs(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for logs relevant to the query."""
        try:
            # First try semantic search
            results = self.search_engine.search(query, n_results=limit)
            if results:
                return results
            
            # Fallback to keyword search
            keywords = self._extract_search_terms(query)
            if keywords:
                # Use the first keyword for a simple search
                results = self.search_engine.search(keywords[0], n_results=limit)
                return results
                
        except Exception as e:
            logger.error(f"Error searching logs: {e}")
        
        return []
    
    def _build_context(self, relevant_logs: List[Dict]) -> str:
        """Build context string from relevant logs."""
        if not relevant_logs:
            return "No relevant logs found."
        
        context_lines = []
        for log in relevant_logs[:5]:  # Limit to 5 most relevant
            timestamp = log.get('ts', '')
            severity = log.get('severity', '')
            unit = log.get('unit', '')
            message = log.get('message', '')
            
            context_lines.append(f"[{timestamp}] {severity.upper()} {unit}: {message}")
        
        return "\n".join(context_lines)
    
    def _generate_response(self, query: str, context: str) -> str:
        """Generate a response using Ollama LLM."""
        try:
            # Build the prompt with context
            prompt = f"""You are a helpful system administrator assistant analyzing log data. 
Use the following log context to answer the user's question. If the context doesn't contain 
enough information, say so and suggest what additional information might be needed.

Log Context:
{context}

User Question: {query}

Provide a clear, concise answer based on the log data. If you see errors or issues, explain 
what they mean and suggest potential solutions."""

            # Call Ollama API
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": "llama2",  # Default model, could be configurable
                    "prompt": prompt,
                    "stream": False
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get('response', 'Unable to generate response.')
            else:
                logger.error(f"Ollama API error: {response.status_code}")
                return "Unable to generate response due to LLM service error."
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling Ollama API: {e}")
            return "Unable to generate response due to LLM service error."
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return "Unable to generate response."
    
    def _calculate_confidence(self, relevant_logs: List[Dict], query: str) -> float:
        """Calculate confidence score for the response."""
        if not relevant_logs:
            return 0.0
        
        # Simple confidence calculation based on number and relevance of logs
        base_confidence = min(len(relevant_logs) / 10.0, 1.0)
        
        # Boost confidence if query terms appear in log messages
        query_lower = query.lower()
        term_matches = 0
        total_logs = len(relevant_logs)
        
        for log in relevant_logs:
            message = log.get('message', '').lower()
            if any(term in message for term in query_lower.split()):
                term_matches += 1
        
        term_boost = term_matches / total_logs if total_logs > 0 else 0
        
        return min(base_confidence + term_boost * 0.3, 1.0)
    
    def chat(self, user_message: str) -> ChatResponse:
        """Process a user message and return a response."""
        start_time = time.time()
        
        # Add user message to history
        self.add_message("user", user_message)
        
        # Search for relevant logs
        relevant_logs = self._search_relevant_logs(user_message)
        
        # Build context from relevant logs
        context = self._build_context(relevant_logs)
        
        # Generate response
        response_text = self._generate_response(user_message, context)
        
        # Calculate confidence
        confidence = self._calculate_confidence(relevant_logs, user_message)
        
        # Add assistant response to history
        self.add_message("assistant", response_text)
        
        query_time = time.time() - start_time
        
        return ChatResponse(
            response=response_text,
            sources=relevant_logs,
            confidence=confidence,
            query_time=query_time
        )
    
    def get_conversation_history(self) -> List[Dict]:
        """Get conversation history as a list of dictionaries."""
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp
            }
            for msg in self.conversation_history
        ]
    
    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history.clear()
    
    def get_system_stats(self) -> Dict:
        """Get system statistics for the chat engine."""
        try:
            # Get basic stats
            total_logs = self.db.execute_query(
                "SELECT COUNT(*) as count FROM logs"
            )[0]['count'] if self.db.execute_query("SELECT COUNT(*) as count FROM logs") else 0
            
            indexed_logs = self.db.execute_query(
                "SELECT COUNT(*) as count FROM log_embeddings"
            )[0]['count'] if self.db.execute_query("SELECT COUNT(*) as count FROM log_embeddings") else 0
            
            # Check Ollama availability
            try:
                response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
                ollama_available = response.status_code == 200
            except:
                ollama_available = False
            
            return {
                "total_logs": total_logs,
                "indexed_logs": indexed_logs,
                "ollama_available": ollama_available,
                "conversation_history_length": len(self.conversation_history)
            }
        except Exception as e:
            logger.error(f"Error getting system stats: {e}")
            return {
                "error": str(e)
            }