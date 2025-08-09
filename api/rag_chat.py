#!/usr/bin/env python3
"""
RAG Chat functionality for Chimera LogMind Core.
Integrates with Ollama for LLM-based chat with log context retrieval.
"""

import json
import datetime as dt
import requests
from typing import List, Dict, Any, Optional
from .embeddings import SemanticSearchEngine
from .db import get_connection


class RAGChatEngine:
    """RAG Chat engine that combines semantic search with LLM responses."""
    
    def __init__(self, db_path: str, ollama_url: str = "http://localhost:11434"):
        self.db_path = db_path
        self.ollama_url = ollama_url
        self.search_engine = SemanticSearchEngine(db_path)
        self.conversation_history: List[Dict[str, str]] = []
        
    def add_to_history(self, role: str, content: str) -> None:
        """Add a message to the conversation history."""
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": dt.datetime.utcnow().isoformat()
        })
        # Keep only last 20 messages to prevent context overflow
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]
    
    def get_relevant_logs(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """Get relevant logs for the query using semantic search."""
        try:
            return self.search_engine.search_logs(
                query=query,
                n_results=n_results,
                since_seconds=86400  # Last 24 hours
            )
        except Exception as e:
            print(f"Error getting relevant logs: {e}")
            return []
    
    def format_context(self, logs: List[Dict[str, Any]]) -> str:
        """Format logs into context for the LLM."""
        if not logs:
            return "No relevant logs found."
        
        context_lines = ["Relevant system logs:"]
        for log in logs:
            context_lines.append(
                f"- [{log.get('ts', 'N/A')}] {log.get('severity', 'INFO')} "
                f"[{log.get('unit', 'unknown')}] {log.get('message', '')}"
            )
        
        return "\n".join(context_lines)
    
    def generate_response(self, query: str, model: str = "llama3.2:3b") -> Dict[str, Any]:
        """Generate a response using RAG with log context."""
        try:
            # Get relevant logs
            relevant_logs = self.get_relevant_logs(query)
            context = self.format_context(relevant_logs)
            
            # Build system prompt
            system_prompt = f"""You are a helpful system administrator assistant for Chimera LogMind Core. 
You have access to recent system logs to help answer questions about system health, security, and troubleshooting.

When responding:
1. Use the provided log context to give accurate, helpful answers
2. If logs show issues, explain what they mean and suggest solutions
3. Be concise but thorough
4. If no relevant logs are found, say so and provide general guidance

Current log context:
{context}

Respond as a helpful system administrator."""
            
            # Build messages for Ollama
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add conversation history
            for msg in self.conversation_history[-10:]:  # Last 10 messages
                messages.append({"role": msg["role"], "content": msg["content"]})
            
            # Add current query
            messages.append({"role": "user", "content": query})
            
            # Call Ollama
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "max_tokens": 1000
                    }
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                assistant_message = result.get("message", {}).get("content", "Sorry, I couldn't generate a response.")
                
                # Add to history
                self.add_to_history("user", query)
                self.add_to_history("assistant", assistant_message)
                
                return {
                    "response": assistant_message,
                    "context_logs": relevant_logs,
                    "model": model,
                    "timestamp": dt.datetime.utcnow().isoformat()
                }
            else:
                error_msg = f"Ollama API error: {response.status_code}"
                self.add_to_history("user", query)
                self.add_to_history("assistant", error_msg)
                
                return {
                    "response": error_msg,
                    "context_logs": [],
                    "model": model,
                    "timestamp": dt.datetime.utcnow().isoformat(),
                    "error": True
                }
                
        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to connect to Ollama: {e}"
            self.add_to_history("user", query)
            self.add_to_history("assistant", error_msg)
            
            return {
                "response": error_msg,
                "context_logs": [],
                "model": model,
                "timestamp": dt.datetime.utcnow().isoformat(),
                "error": True
            }
        except Exception as e:
            error_msg = f"Error generating response: {e}"
            self.add_to_history("user", query)
            self.add_to_history("assistant", error_msg)
            
            return {
                "response": error_msg,
                "context_logs": [],
                "model": model,
                "timestamp": dt.datetime.utcnow().isoformat(),
                "error": True
            }
    
    def get_conversation_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent conversation history."""
        return self.conversation_history[-limit:] if limit > 0 else self.conversation_history
    
    def clear_history(self) -> None:
        """Clear conversation history."""
        self.conversation_history = []
    
    def get_available_models(self) -> List[str]:
        """Get list of available Ollama models."""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=10)
            if response.status_code == 200:
                result = response.json()
                return [model["name"] for model in result.get("models", [])]
            else:
                return []
        except Exception:
            return []
    
    def check_ollama_health(self) -> Dict[str, Any]:
        """Check if Ollama is available and healthy."""
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "url": self.ollama_url,
                    "available_models": self.get_available_models()
                }
            else:
                return {
                    "status": "error",
                    "url": self.ollama_url,
                    "error": f"HTTP {response.status_code}"
                }
        except Exception as e:
            return {
                "status": "unavailable",
                "url": self.ollama_url,
                "error": str(e)
            }