"""
Session Manager for Madhushala Excise Bridge
Tracks automation sessions and selected items
"""
import uuid
from datetime import datetime
from typing import Dict, Optional, List
import logging

logger = logging.getLogger("madhushala-excise-bridge")

class AutomationSession:
    """Represents an automation session"""
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.created_at = datetime.utcnow()
        self.ended_at: Optional[datetime] = None
        self.status = "active"
        self.browser_status = "disconnected"
        self.current_url: Optional[str] = None
        self.selected_items: Dict[str, dict] = {}
        self.committed_batches: List[str] = []
        self.last_error: Optional[str] = None

class SessionManager:
    """Manages automation sessions"""
    _active_session: Optional[AutomationSession] = None
    _sessions: Dict[str, AutomationSession] = {}
    
    @classmethod
    def create_session(cls) -> str:
        """Create a new automation session"""
        session_id = str(uuid.uuid4())
        session = AutomationSession(session_id)
        cls._sessions[session_id] = session
        cls._active_session = session
        logger.info(f"Session created: {session_id}")
        return session_id
    
    @classmethod
    def get_session(cls, session_id: str) -> Optional[AutomationSession]:
        """Get a session by ID"""
        return cls._sessions.get(session_id)
    
    @classmethod
    def end_session(cls) -> None:
        """End the current session"""
        if cls._active_session:
            cls._active_session.ended_at = datetime.utcnow()
            cls._active_session.status = "ended"
            logger.info(f"Session ended: {cls._active_session.session_id}")
            cls._active_session = None
    
    @classmethod
    def add_selected_item(cls, item: dict) -> None:
        """Add an item to the current session's selected items"""
        if cls._active_session:
            cls._active_session.selected_items[item['canonical_key']] = item
            logger.info(f"Item added to selection: {item['canonical_key']}")
    
    @classmethod
    def remove_selected_item(cls, canonical_key: str) -> None:
        """Remove an item from the current session's selected items"""
        if cls._active_session and canonical_key in cls._active_session.selected_items:
            del cls._active_session.selected_items[canonical_key]
            logger.info(f"Item removed from selection: {canonical_key}")
    
    @classmethod
    def get_selected_items(cls) -> Dict[str, dict]:
        """Get all selected items from the current session"""
        if cls._active_session:
            return cls._active_session.selected_items
        return {}
    
    @classmethod
    def clear_selected_items(cls) -> None:
        """Clear all selected items from the current session"""
        if cls._active_session:
            cls._active_session.selected_items.clear()
            logger.info("Selected items cleared")
    
    @classmethod
    def get_session_id(cls) -> Optional[str]:
        """Get the current session ID"""
        if cls._active_session:
            return cls._active_session.session_id
        return None
    
    @classmethod
    def get_last_error(cls) -> Optional[str]:
        """Get the last error from the current session"""
        if cls._active_session:
            return cls._active_session.last_error
        return None
    
    @classmethod
    def set_last_error(cls, error: str) -> None:
        """Set the last error for the current session"""
        if cls._active_session:
            cls._active_session.last_error = error
            logger.error(f"Session error: {error}")
    
    @classmethod
    def start(cls) -> None:
        """Initialize the session manager"""
        logger.info("Session manager started")
    
    @classmethod
    def stop(cls) -> None:
        """Stop the session manager"""
        cls.end_session()
        logger.info("Session manager stopped")
</arg_value>
<task_progress>
- [x] Create main application file
- [x] Implement Playwright automation for manual consent
- [x] Create static HTML demo page
- [x] Implement Madhushala API client
- [x] Create configuration file
- [x] Create database models
- [x] Create database connection module
- [x] Initialize git repository
- [x] Create requirements.txt
- [x] Create .env.example
- [x] Create README.md
- [x] Create style.css
- [x] Create app.js
- [x] Create normalizer module
- [x] Create test files
- [ ] Run syntax checks
- [ ] Run pytest
- [ ] Verify server starts
- [ ] Verify /health endpoint
- [ ] Verify demo page loads
- [ ] Verify Playwright launches
- [ ] Create IMPLEMENTATION_STATUS.md
- [ ] Commit and push to GitHub
</task_progress>
</write_to_file>