#!/usr/bin/env python3
"""
RAG Chat Module for Chimera LogMind

Provides conversational log analysis using RAG (Retrieval-Augmented Generation)
with local LLM integration via Ollama.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import duckdb

try:
    from .db import get_connection
    from .embeddings import SemanticSearchEngine
    from .config import ChimeraConfig
except ImportError:
    from db import get_connection
    from embeddings import SemanticSearchEngine
    from config import ChimeraConfig

logger = logging.getLogger(__name__)


class RAGChatEngine:
    """RAG Chat Engine for conversational log analysis"""
    
    def __init__(self, db_path: str, config: ChimeraConfig):
        self.db_path = db_path
        self.config = config
        self.search_engine = SemanticSearchEngine(db_path, config)
        self.chat_history: List[Dict[str, Any]] = []
        
    def _extract_time_range(self, query: str) -> Tuple[Optional[int], Optional[int]]:
        """Extract time range from natural language query"""
        query_lower = query.lower()
        
        # Common time patterns
        time_patterns = {
            r'last (\d+) (hour|hours)': lambda x: int(x) * 3600,
            r'last (\d+) (day|days)': lambda x: int(x) * 86400,
            r'last (\d+) (minute|minutes)': lambda x: int(x) * 60,
            r'last (\d+) (week|weeks)': lambda x: int(x) * 604800,
            r'(\d+) (hour|hours) ago': lambda x: int(x) * 3600,
            r'(\d+) (day|days) ago': lambda x: int(x) * 86400,
            r'(\d+) (minute|minutes) ago': lambda x: int(x) * 60,
            r'(\d+) (week|weeks) ago': lambda x: int(x) * 604800,
        }
        
        for pattern, converter in time_patterns.items():
            match = re.search(pattern, query_lower)
            if match:
                return converter(match.group(1)), None
                
        return None, None
    
    def _extract_filters(self, query: str) -> Dict[str, Any]:
        """Extract filters from natural language query"""
        filters = {}
        query_lower = query.lower()
        
        # Extract severity levels
        severity_patterns = {
            'error': 'err',
            'errors': 'err', 
            'warning': 'warning',
            'warnings': 'warning',
            'critical': 'crit',
            'debug': 'debug',
            'info': 'info',
            'notice': 'notice',
            'alert': 'alert',
            'emergency': 'emerg'
        }
        
        for pattern, severity in severity_patterns.items():
            if pattern in query_lower:
                filters['severity'] = severity
                break
        
        # Extract source types
        source_patterns = {
            'journal': 'journald',
            'journald': 'journald',
            'systemd': 'journald',
            'file': 'file',
            'log file': 'file',
            'container': 'container',
            'docker': 'container'
        }
        
        for pattern, source in source_patterns.items():
            if pattern in query_lower:
                filters['source'] = source
                break
        
        # Extract unit/service names
        unit_match = re.search(r'(service|unit)\s+([a-zA-Z0-9\-\.]+)', query_lower)
        if unit_match:
            filters['unit'] = unit_match.group(2)
        
        return filters
    
    def _get_relevant_logs(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get relevant logs for the query using semantic search"""
        try:
            # Extract time range and filters
            since_seconds, _ = self._extract_time_range(query)
            filters = self._extract_filters(query)
            
            # Perform semantic search
            results = self.search_engine.search(
                query=query,
                n_results=limit,
                since=since_seconds,
                **filters
            )
            
            return results
        except Exception as e:
            logger.error(f"Error getting relevant logs: {e}")
            return []
    
    def _get_system_context(self, since_seconds: Optional[int] = None) -> Dict[str, Any]:
        """Get system context for the chat"""
        try:
            db_conn = get_connection(self.db_path)
            
            # Get recent metrics
            metrics_query = """
            SELECT 
                metric_type,
                AVG(value) as avg_value,
                MAX(value) as max_value,
                COUNT(*) as count
            FROM system_metrics 
            WHERE timestamp >= datetime('now', '-{} seconds')
            GROUP BY metric_type
            """.format(since_seconds or 3600)
            
            metrics = {}
            for row in db_conn.execute(metrics_query).fetchall():
                metrics[row[0]] = {
                    'avg': row[1],
                    'max': row[2], 
                    'count': row[3]
                }
            
            # Get recent alerts
            alerts_query = """
            SELECT 
                severity,
                message,
                timestamp
            FROM system_alerts 
            WHERE timestamp >= datetime('now', '-{} seconds')
            ORDER BY timestamp DESC
            LIMIT 10
            """.format(since_seconds or 3600)
            
            alerts = []
            for row in db_conn.execute(alerts_query).fetchall():
                alerts.append({
                    'severity': row[0],
                    'message': row[1],
                    'timestamp': row[2]
                })
            
            # Get log statistics
            log_stats_query = """
            SELECT 
                severity,
                source,
                COUNT(*) as count
            FROM logs 
            WHERE timestamp >= datetime('now', '-{} seconds')
            GROUP BY severity, source
            ORDER BY count DESC
            """.format(since_seconds or 3600)
            
            log_stats = {}
            for row in db_conn.execute(log_stats_query).fetchall():
                severity, source, count = row
                if source not in log_stats:
                    log_stats[source] = {}
                log_stats[source][severity] = count
            
            db_conn.close()
            
            return {
                'metrics': metrics,
                'alerts': alerts,
                'log_stats': log_stats
            }
            
        except Exception as e:
            logger.error(f"Error getting system context: {e}")
            return {}
    
    def _format_context_for_llm(self, logs: List[Dict], context: Dict) -> str:
        """Format context for LLM consumption"""
        context_parts = []
        
        # Add log samples
        if logs:
            context_parts.append("Recent relevant logs:")
            for i, log in enumerate(logs[:10], 1):
                context_parts.append(f"{i}. [{log.get('severity', 'unknown')}] {log.get('message', '')}")
                context_parts.append(f"   Source: {log.get('source', 'unknown')}, Unit: {log.get('unit', 'unknown')}")
                context_parts.append(f"   Time: {log.get('timestamp', 'unknown')}")
                context_parts.append("")
        
        # Add system metrics summary
        if context.get('metrics'):
            context_parts.append("System metrics summary:")
            for metric_type, data in context['metrics'].items():
                context_parts.append(f"- {metric_type}: avg={data['avg']:.2f}, max={data['max']:.2f}")
            context_parts.append("")
        
        # Add recent alerts
        if context.get('alerts'):
            context_parts.append("Recent system alerts:")
            for alert in context['alerts'][:5]:
                context_parts.append(f"- [{alert['severity']}] {alert['message']}")
            context_parts.append("")
        
        # Add log statistics
        if context.get('log_stats'):
            context_parts.append("Log volume by source and severity:")
            for source, severities in context['log_stats'].items():
                context_parts.append(f"- {source}:")
                for severity, count in severities.items():
                    context_parts.append(f"  {severity}: {count}")
            context_parts.append("")
        
        return "\n".join(context_parts)
    
    def chat(self, user_query: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Process a chat query and return response"""
        try:
            # Get relevant logs and context
            relevant_logs = self._get_relevant_logs(user_query)
            since_seconds, _ = self._extract_time_range(user_query)
            system_context = self._get_system_context(since_seconds)
            
            # Format context for LLM
            context_str = self._format_context_for_llm(relevant_logs, system_context)
            
            # Create prompt for LLM
            prompt = f"""You are a helpful log analysis assistant for a Linux system monitoring tool called Chimera LogMind.

Context information:
{context_str}

User question: {user_query}

Please provide a helpful analysis based on the logs and system context. Focus on:
1. Identifying patterns or anomalies in the logs
2. Explaining what the logs indicate about system health
3. Suggesting potential issues or areas of concern
4. Providing actionable insights

Keep your response concise but informative. If you notice any concerning patterns, highlight them clearly."""

            # For now, we'll return a structured response
            # In a full implementation, this would call Ollama or another LLM
            response = self._generate_llm_response(prompt, user_query, relevant_logs, system_context)
            
            # Store chat history
            chat_entry = {
                'session_id': session_id,
                'timestamp': datetime.now().isoformat(),
                'user_query': user_query,
                'response': response,
                'relevant_logs_count': len(relevant_logs),
                'context_summary': {
                    'metrics_count': len(system_context.get('metrics', {})),
                    'alerts_count': len(system_context.get('alerts', {})),
                    'log_sources': list(system_context.get('log_stats', {}).keys())
                }
            }
            
            self.chat_history.append(chat_entry)
            
            return {
                'response': response,
                'relevant_logs_count': len(relevant_logs),
                'context_summary': chat_entry['context_summary'],
                'session_id': session_id
            }
            
        except Exception as e:
            logger.error(f"Error in chat: {e}")
            return {
                'response': f"Sorry, I encountered an error while processing your query: {str(e)}",
                'error': str(e)
            }
    
    def _generate_llm_response(self, prompt: str, user_query: str, logs: List[Dict], context: Dict) -> str:
        """Generate LLM response (placeholder implementation)"""
        # This is a simplified response generator
        # In production, this would integrate with Ollama or another LLM
        
        # Analyze the query and logs to generate a meaningful response
        error_count = sum(1 for log in logs if log.get('severity') in ['err', 'crit', 'alert', 'emerg'])
        warning_count = sum(1 for log in logs if log.get('severity') == 'warning')
        
        response_parts = []
        
        if "error" in user_query.lower() or "problem" in user_query.lower():
            if error_count > 0:
                response_parts.append(f"I found {error_count} error-level logs in the recent data.")
                response_parts.append("Key error patterns:")
                for log in logs[:5]:
                    if log.get('severity') in ['err', 'crit', 'alert', 'emerg']:
                        response_parts.append(f"- {log.get('message', '')[:100]}...")
            else:
                response_parts.append("No critical errors found in the recent logs.")
        
        elif "performance" in user_query.lower() or "metrics" in user_query.lower():
            if context.get('metrics'):
                response_parts.append("System performance overview:")
                for metric_type, data in context['metrics'].items():
                    response_parts.append(f"- {metric_type}: Average {data['avg']:.2f}, Peak {data['max']:.2f}")
        
        elif "alert" in user_query.lower():
            if context.get('alerts'):
                response_parts.append(f"Found {len(context['alerts'])} recent alerts:")
                for alert in context['alerts'][:3]:
                    response_parts.append(f"- [{alert['severity']}] {alert['message']}")
            else:
                response_parts.append("No recent alerts detected.")
        
        else:
            # General analysis
            response_parts.append(f"Analysis of {len(logs)} relevant logs:")
            response_parts.append(f"- Errors: {error_count}")
            response_parts.append(f"- Warnings: {warning_count}")
            
            if logs:
                response_parts.append("Recent log activity:")
                for log in logs[:3]:
                    response_parts.append(f"- [{log.get('severity', 'unknown')}] {log.get('message', '')[:80]}...")
        
        return "\n".join(response_parts)
    
    def get_chat_history(self, session_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get chat history for a session"""
        if session_id:
            return [entry for entry in self.chat_history if entry.get('session_id') == session_id][-limit:]
        return self.chat_history[-limit:]
    
    def clear_chat_history(self, session_id: Optional[str] = None) -> bool:
        """Clear chat history"""
        try:
            if session_id:
                self.chat_history = [entry for entry in self.chat_history if entry.get('session_id') != session_id]
            else:
                self.chat_history.clear()
            return True
        except Exception as e:
            logger.error(f"Error clearing chat history: {e}")
            return False