"""Capture Service for Madhushala Excise Bridge Phase 1."""
import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
import logging

from app.automation.row_parser import normalize_raw_item, normalize_raw_items
from app.config import settings

logger = logging.getLogger("madhushala-excise-bridge")

class CaptureService:
    """Handles capturing, normalizing, and storing excise data."""

    def __init__(self):
        self.latest_capture: Optional[Dict] = None
        self.all_captures: List[Dict] = []

    async def initialize(self) -> None:
        """Initialize the capture service"""
        os.makedirs(settings.CAPTURES_DIR, exist_ok=True)

        # Load any existing captures
        await self._load_existing_captures()

    async def _load_existing_captures(self) -> None:
        """Load existing capture files"""
        try:
            captures_dir = settings.CAPTURES_DIR
            if os.path.exists(captures_dir):
                for filename in sorted(os.listdir(captures_dir)):
                    if filename.endswith(".json"):
                        filepath = os.path.join(captures_dir, filename)
                        try:
                            with open(filepath, 'r', encoding='utf-8') as f:
                                capture = json.load(f)
                                self.all_captures.append(capture)
                                self.latest_capture = capture
                        except Exception as e:
                            logger.warning(f"Failed to load capture {filename}: {e}")
        except Exception as e:
            logger.error(f"Error loading existing captures: {e}")

    async def save_capture(self, raw_batch: Dict) -> str:
        """Save a capture batch with normalized data"""
        try:
            captured_at = raw_batch.get("capturedAt") or datetime.now(timezone.utc).isoformat()
            normalized_items = normalize_raw_items(raw_batch.get("items", []), captured_at=captured_at)

            batch_id = raw_batch.get("batchId")
            if not batch_id:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                batch_id = f"{stamp}_{uuid.uuid4().hex[:8]}"

            batch = {
                "batchId": batch_id,
                "capturedAt": captured_at,
                "pageUrl": raw_batch.get("pageUrl", ""),
                "itemCount": len(normalized_items),
                "items": normalized_items
            }

            filename = os.path.join(settings.CAPTURES_DIR, f"{batch['batchId']}.json")
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(batch, f, indent=2, ensure_ascii=False)

            # Update in-memory storage
            self.latest_capture = batch
            self.all_captures.append(batch)

            logger.info(f"Saved capture batch {batch['batchId']} with {len(normalized_items)} items")
            return batch['batchId']

        except Exception as e:
            logger.error(f"Failed to save capture: {e}")
            raise

    def _normalize_item(self, raw_item: Dict) -> Optional[Dict]:
        """Normalize a single item from raw browser data"""
        return normalize_raw_item(raw_item)

    def get_latest_capture(self) -> Optional[Dict]:
        """Get the latest capture batch"""
        return self.latest_capture

    def get_all_captures(self) -> List[Dict]:
        """Get all capture batches (summaries only)"""
        summaries = []
        for capture in self.all_captures:
            summaries.append({
                "batchId": capture["batchId"],
                "capturedAt": capture["capturedAt"],
                "pageUrl": capture["pageUrl"],
                "itemCount": capture["itemCount"]
            })
        return summaries

    async def shutdown(self) -> None:
        """Shutdown the capture service"""
        # No special cleanup needed for Phase 1
        pass
