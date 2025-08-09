#!/usr/bin/env python3
import json
import hashlib
import datetime as dt
import subprocess
import requests
from typing import List, Dict, Any, Optional, Tuple
import threading
import time

from .db import get_connection


class OllamaEmbeddingClient:
    """Client for Ollama embedding API"""
    
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nomic-embed-text"):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.session = requests.Session()
        self.session.timeout = 30
    
    def get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding for a text string"""
        try:
            response = self.session.post(
                f"{self.base_url}/api/embeddings",
                json={
                    "model": self.model,
                    "prompt": text
                }
            )
            response.raise_for_status()
            data = response.json()
            return data.get("embedding")
        except Exception as e:
            print(f"Error getting embedding: {e}")
            return None
    
    def get_embeddings_batch(self, texts: List[str], batch_size: int = 10) -> List[Optional[List[float]]]:
        """Get embeddings for multiple texts in batches"""
        embeddings = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = []
            
            for text in batch:
                embedding = self.get_embedding(text)
                batch_embeddings.append(embedding)
            
            embeddings.extend(batch_embeddings)
            time.sleep(0.1)  # Rate limiting
        
        return embeddings


class ChromaDBClient:
    """Client for ChromaDB vector database"""
    
    def __init__(self, persist_directory: str = "/var/lib/chimera/chromadb"):
        self.persist_directory = persist_directory
        self.collection_name = "log_embeddings"
        self._client = None
        self._collection = None
        self._lock = threading.Lock()
    
    def _get_client(self):
        """Get ChromaDB client (lazy initialization)"""
        if self._client is None:
            try:
                import chromadb
                from chromadb.config import Settings
                
                self._client = chromadb.PersistentClient(
                    path=self.persist_directory,
                    settings=Settings(
                        anonymized_telemetry=False,
                        allow_reset=True
                    )
                )
            except ImportError:
                raise RuntimeError("ChromaDB not installed. Install with: pip install chromadb")
        
        return self._client
    
    def _get_collection(self):
        """Get or create the embeddings collection"""
        if self._collection is None:
            client = self._get_client()
            
            try:
                self._collection = client.get_collection(self.collection_name)
            except Exception:
                # Collection doesn't exist, create it
                self._collection = client.create_collection(
                    name=self.collection_name,
                    metadata={"description": "Log message embeddings for semantic search"}
                )
        
        return self._collection
    
    def add_embeddings(self, ids: List[str], embeddings: List[List[float]], 
                      metadatas: List[Dict[str, Any]], documents: List[str]) -> None:
        """Add embeddings to the collection"""
        with self._lock:
            collection = self._get_collection()
            collection.add(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents
            )
    
    def search(self, query_embedding: List[float], n_results: int = 10, 
               where: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Search for similar embeddings"""
        with self._lock:
            collection = self._get_collection()
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where
            )
            return results
    
    def delete_embeddings(self, ids: List[str]) -> None:
        """Delete embeddings by IDs"""
        with self._lock:
            collection = self._get_collection()
            collection.delete(ids=ids)


class SemanticSearchEngine:
    """Semantic search engine for log messages"""
    
    def __init__(self, db_path: Optional[str] = None, 
                 ollama_url: str = "http://localhost:11434",
                 ollama_model: str = "nomic-embed-text",
                 chroma_persist_dir: str = "/var/lib/chimera/chromadb"):
        self.db_path = db_path
        self.embedding_client = OllamaEmbeddingClient(ollama_url, ollama_model)
        self.chroma_client = ChromaDBClient(chroma_persist_dir)
        self._lock = threading.Lock()
    
    def index_logs(self, log_ids: Optional[List[int]] = None, 
                   since_seconds: int = 86400) -> Tuple[int, int]:
        """Index logs for semantic search"""
        conn = get_connection(self.db_path)
        try:
            # Get logs to index
            if log_ids:
                placeholders = ','.join(['?' for _ in log_ids])
                sql = f"""
                    SELECT id, ts, hostname, source, unit, severity, message 
                    FROM logs 
                    WHERE id IN ({placeholders}) AND id NOT IN (
                        SELECT log_id FROM log_embeddings
                    )
                """
                params = log_ids
            else:
                since_ts = dt.datetime.utcnow() - dt.timedelta(seconds=since_seconds)
                sql = """
                    SELECT id, ts, hostname, source, unit, severity, message 
                    FROM logs 
                    WHERE ts >= ? AND id NOT IN (
                        SELECT log_id FROM log_embeddings
                    )
                    ORDER BY ts DESC
                    LIMIT 1000
                """
                params = [since_ts]
            
            cur = conn.cursor()
            cur.execute(sql, params)
            logs = cur.fetchall()
            
            if not logs:
                return (0, 0)
            
            # Prepare data for embedding
            texts = []
            metadatas = []
            documents = []
            ids = []
            
            for log_id, ts, hostname, source, unit, severity, message in logs:
                # Create searchable text
                search_text = f"{unit}: {message}"
                if hostname:
                    search_text = f"[{hostname}] {search_text}"
                
                texts.append(search_text)
                metadatas.append({
                    "log_id": log_id,
                    "ts": ts.isoformat() if ts else None,
                    "hostname": hostname,
                    "source": source,
                    "unit": unit,
                    "severity": severity,
                })
                documents.append(message or "")
                ids.append(f"log_{log_id}")
            
            # Get embeddings
            embeddings = self.embedding_client.get_embeddings_batch(texts)
            
            # Filter out failed embeddings
            valid_data = []
            for i, embedding in enumerate(embeddings):
                if embedding is not None:
                    valid_data.append((ids[i], embedding, metadatas[i], documents[i]))
            
            if not valid_data:
                return (0, 0)
            
            # Add to ChromaDB
            ids, embeddings, metadatas, documents = zip(*valid_data)
            self.chroma_client.add_embeddings(list(ids), list(embeddings), list(metadatas), list(documents))
            
            # Record in database
            for log_id, _, _, _ in valid_data:
                conn.execute(
                    "INSERT OR IGNORE INTO log_embeddings (log_id, indexed_at) VALUES (?, CURRENT_TIMESTAMP)",
                    [int(log_id.split('_')[1])]
                )
            
            return (len(valid_data), len(logs))
            
        finally:
            conn.close()
    
    def search_logs(self, query: str, n_results: int = 10, 
                   since_seconds: Optional[int] = None,
                   source: Optional[str] = None,
                   unit: Optional[str] = None,
                   severity: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search logs semantically"""
        # Get query embedding
        query_embedding = self.embedding_client.get_embedding(query)
        if not query_embedding:
            return []
        
        # Build where clause for ChromaDB
        where_clause = {}
        if since_seconds:
            since_ts = dt.datetime.utcnow() - dt.timedelta(seconds=since_seconds)
            where_clause["ts"] = {"$gte": since_ts.isoformat()}
        if source:
            where_clause["source"] = source
        if unit:
            where_clause["unit"] = unit
        if severity:
            where_clause["severity"] = severity
        
        # Search ChromaDB
        results = self.chroma_client.search(query_embedding, n_results, where_clause)
        
        # Get full log details from database
        if not results.get("ids") or not results["ids"][0]:
            return []
        
        log_ids = [int(metadata["log_id"]) for metadata in results["metadatas"][0]]
        
        conn = get_connection(self.db_path)
        try:
            placeholders = ','.join(['?' for _ in log_ids])
            sql = f"""
                SELECT ts, hostname, source, unit, severity, pid, message 
                FROM logs 
                WHERE id IN ({placeholders})
                ORDER BY ts DESC
            """
            cur = conn.cursor()
            cur.execute(sql, log_ids)
            logs = cur.fetchall()
            
            # Combine with search results
            search_results = []
            for i, (ts, hostname, src, unit, sev, pid, message) in enumerate(logs):
                if i < len(results["distances"][0]):
                    search_results.append({
                        "ts": ts.isoformat() if ts else None,
                        "hostname": hostname,
                        "source": src,
                        "unit": unit,
                        "severity": sev,
                        "pid": pid,
                        "message": message,
                        "similarity": 1.0 - results["distances"][0][i],  # Convert distance to similarity
                    })
            
            return search_results
            
        finally:
            conn.close()
    
    def cleanup_old_embeddings(self, days: int = 30) -> int:
        """Clean up old embeddings"""
        cutoff_date = dt.datetime.utcnow() - dt.timedelta(days=days)
        
        conn = get_connection(self.db_path)
        try:
            # Get old embedding IDs
            cur = conn.cursor()
            cur.execute(
                "SELECT log_id FROM log_embeddings WHERE indexed_at < ?",
                [cutoff_date]
            )
            old_ids = [f"log_{row[0]}" for row in cur.fetchall()]
            
            if old_ids:
                # Delete from ChromaDB
                self.chroma_client.delete_embeddings(old_ids)
                
                # Delete from database
                conn.execute(
                    "DELETE FROM log_embeddings WHERE indexed_at < ?",
                    [cutoff_date]
                )
            
            return len(old_ids)
            
        finally:
            conn.close()


class AnomalyDetector:
    """Simple anomaly detection for log patterns"""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
    
    def detect_anomalies(self, since_seconds: int = 3600) -> List[Dict[str, Any]]:
        """Detect anomalies in recent logs"""
        conn = get_connection(self.db_path)
        try:
            since_ts = dt.datetime.utcnow() - dt.timedelta(seconds=since_seconds)
            
            anomalies = []
            
            # 1. Detect unusual error spikes
            cur = conn.cursor()
            cur.execute("""
                SELECT unit, COUNT(*) as error_count
                FROM logs 
                WHERE ts >= ? AND severity IN ('err', 'crit', 'emerg')
                GROUP BY unit
                HAVING error_count > 10
                ORDER BY error_count DESC
            """, [since_ts])
            
            for unit, error_count in cur.fetchall():
                anomalies.append({
                    "type": "error_spike",
                    "unit": unit,
                    "count": error_count,
                    "severity": "high",
                    "description": f"High error rate in {unit}: {error_count} errors"
                })
            
            # 2. Detect unusual log volume
            cur.execute("""
                SELECT source, COUNT(*) as log_count
                FROM logs 
                WHERE ts >= ? 
                GROUP BY source
                HAVING log_count > 1000
                ORDER BY log_count DESC
            """, [since_ts])
            
            for source, log_count in cur.fetchall():
                anomalies.append({
                    "type": "high_volume",
                    "source": source,
                    "count": log_count,
                    "severity": "medium",
                    "description": f"High log volume from {source}: {log_count} logs"
                })
            
            # 3. Detect missing expected logs
            cur.execute("""
                SELECT unit, MAX(ts) as last_seen
                FROM logs 
                WHERE unit IN ('systemd', 'sshd', 'cron') AND ts >= ?
                GROUP BY unit
            """, [since_ts])
            
            expected_units = {'systemd', 'sshd', 'cron'}
            seen_units = {row[0] for row in cur.fetchall()}
            missing_units = expected_units - seen_units
            
            for unit in missing_units:
                anomalies.append({
                    "type": "missing_logs",
                    "unit": unit,
                    "severity": "medium",
                    "description": f"Missing expected logs from {unit}"
                })
            
            return anomalies
            
        finally:
            conn.close()


class RAGChatEngine:
    """RAG (Retrieval-Augmented Generation) chat engine for log analysis"""
    
    def __init__(self, db_path: Optional[str] = None, 
                 ollama_url: str = "http://localhost:11434",
                 ollama_model: str = "qwen3:1.7b",
                 chroma_persist_dir: str = "/var/lib/chimera/chromadb"):
        self.db_path = db_path
        self.ollama_url = ollama_url.rstrip('/')
        self.ollama_model = ollama_model
        self.search_engine = SemanticSearchEngine(
            db_path=db_path,
            ollama_url=ollama_url,
            ollama_model="nomic-embed-text",
            chroma_persist_dir=chroma_persist_dir
        )
        self.session = requests.Session()
        self.session.timeout = 60
    
    def _call_ollama(self, prompt: str) -> Optional[str]:
        """Call Ollama LLM API"""
        try:
            response = self.session.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False
                }
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
        except Exception as e:
            print(f"Error calling Ollama: {e}")
            return None
    
    def _get_relevant_logs(self, query: str, context_size: int = 10, since_seconds: int = 3600) -> List[Dict[str, Any]]:
        """Get relevant logs for the query"""
        try:
            return self.search_engine.search_logs(
                query=query,
                n_results=context_size,
                since_seconds=since_seconds
            )
        except Exception as e:
            print(f"Error searching logs: {e}")
            return []
    
    def _format_log_context(self, logs: List[Dict[str, Any]]) -> str:
        """Format logs as context for the LLM"""
        if not logs:
            return "No relevant logs found."
        
        context_lines = ["Relevant log entries:"]
        for i, log in enumerate(logs, 1):
            context_lines.append(
                f"{i}. [{log.get('ts', '')}] {log.get('unit', '')} ({log.get('severity', '')}): {log.get('message', '')}"
            )
        
        return "\n".join(context_lines)
    
    def chat(self, query: str, context_size: int = 10, since_seconds: int = 3600) -> str:
        """Single chat interaction with log context"""
        # Get relevant logs
        relevant_logs = self._get_relevant_logs(query, context_size, since_seconds)
        log_context = self._format_log_context(relevant_logs)
        
        # Create prompt
        prompt = f"""You are a helpful assistant analyzing system logs. Use the following log entries to answer the user's question.

{log_context}

User question: {query}

Please provide a clear, concise answer based on the log data. If the logs don't contain relevant information, say so. Focus on:
- Identifying patterns or issues
- Explaining what the logs indicate
- Suggesting potential causes or solutions
- Highlighting any concerning patterns

Answer:"""
        
        # Get response from LLM
        response = self._call_ollama(prompt)
        if response is None:
            return "Sorry, I couldn't generate a response. Please check if Ollama is running and the model is available."
        
        return response
    
    def start_session(self, context_size: int = 10, since_seconds: int = 3600) -> Dict[str, Any]:
        """Start an interactive chat session"""
        return {
            "session_id": f"chat_{int(time.time())}",
            "context_size": context_size,
            "since_seconds": since_seconds,
            "instructions": "Use 'chimera chat --query \"your question\"' to ask questions about the logs.",
            "example_queries": [
                "What errors occurred in the last hour?",
                "Show me authentication failures",
                "Are there any unusual patterns in the systemd logs?",
                "What services are having issues?",
                "Analyze the network connection logs"
            ]
        }
    
    def interactive_chat(self, context_size: int = 10, since_seconds: int = 3600) -> None:
        """Interactive chat session (for future CLI enhancement)"""
        print("Chimera LogMind Chat Session")
        print("Type 'quit' to exit, 'help' for examples")
        print("-" * 50)
        
        session_info = self.start_session(context_size, since_seconds)
        print(f"Session started with context size: {context_size}, time window: {since_seconds}s")
        print()
        
        while True:
            try:
                query = input("You: ").strip()
                if not query:
                    continue
                
                if query.lower() in ['quit', 'exit', 'q']:
                    break
                
                if query.lower() == 'help':
                    print("\nExample queries:")
                    for example in session_info["example_queries"]:
                        print(f"  - {example}")
                    print()
                    continue
                
                print("Analyzing logs...")
                response = self.chat(query, context_size, since_seconds)
                print(f"Assistant: {response}\n")
                
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                print(f"Error: {e}")
    
    def get_chat_history(self) -> List[Dict[str, Any]]:
        """Get chat history from database"""
        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    session_id TEXT
                )
            """)
            
            cur = conn.execute("""
                SELECT timestamp, role, content, session_id
                FROM chat_history 
                ORDER BY timestamp DESC
                LIMIT 50
            """)
            
            history = []
            for row in cur.fetchall():
                history.append({
                    "timestamp": row[0],
                    "role": row[1],
                    "content": row[2],
                    "session_id": row[3]
                })
            
            return history
            
        finally:
            conn.close()
    
    def clear_chat_history(self) -> None:
        """Clear chat history from database"""
        conn = get_connection(self.db_path)
        try:
            conn.execute("DELETE FROM chat_history")
        finally:
            conn.close()
    
    def get_chat_stats(self) -> Dict[str, Any]:
        """Get chat statistics"""
        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    session_id TEXT
                )
            """)
            
            # Get total messages
            cur = conn.execute("SELECT COUNT(*) FROM chat_history")
            total_messages = cur.fetchone()[0]
            
            # Get messages by role
            cur = conn.execute("SELECT role, COUNT(*) FROM chat_history GROUP BY role")
            messages_by_role = dict(cur.fetchall())
            
            # Get recent activity
            cur = conn.execute("""
                SELECT COUNT(*) FROM chat_history 
                WHERE timestamp >= datetime('now', '-1 hour')
            """)
            recent_messages = cur.fetchone()[0]
            
            return {
                "total_messages": total_messages,
                "messages_by_role": messages_by_role,
                "recent_messages_1h": recent_messages
            }
            
        finally:
            conn.close()