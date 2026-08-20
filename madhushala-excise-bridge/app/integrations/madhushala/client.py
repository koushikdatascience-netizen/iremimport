"""
Madhushala Client for Excise Item Integration
Handles API interactions with Madhushala CRM
"""
import httpx
from typing import Dict, List, Optional
import logging
from datetime import datetime
from pydantic import BaseModel

logger = logging.getLogger("madhushala-excise-bridge")

class MadhushalaClient:
    """Client for interacting with Madhushala CRM API"""
    
    def __init__(self, base_url: str, shop_code: str, token: str):
        self.base_url = base_url
        self.shop_code = shop_code
        self.token = token
        self.client = httpx.AsyncClient()
        
        # Pydantic models
        class ExciseItemResponse(BaseModel):
            itemCode: int
            itemName: str
            t1: str
            t2: str
            t3: str
            t4: str
            
        class UnmappedItemsResponse(BaseModel):
            items: List[Dict[str, any]]
            
        class MappingRequest(BaseModel):
            exciseItemCode: int
            itemCode: str
            
    async def save_excise_item(self, item_data: Dict[str, any]) -> Optional[ExciseItemResponse]:
        """Save excise item to Madhushala CRM"""
        try:
            url = f"{self.base_url}/api/excise-import/ExciseItemMasterSave?shopCode={self.shop_code}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            # Map to required fields (t1-t4)
            payload = {
                "itemName": item_data['brand'],
                "t1": item_data.get('measure_ml', ''),
                "t2": item_data.get('package_type', ''),
                "t3": item_data.get('retailer_margin', ''),
                "t4": item_data.get('supplier', '')
            }
            
            response = await self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            # Parse response
            return ExciseItemResponse(**response.json())
        except Exception as e:
            logger.error(f"Failed to save excise item: {e}")
            return None
    
    async def get_unmapped_items(self) -> List[Dict[str, any]]:
        """Get list of unmapped items from Madhushala CRM"""
        try:
            url = f"{self.base_url}/api/excise-import/unmapped-items?shopCode={self.shop_code}"
            headers = {
                "Authorization": f"Bearer {self.token}"
            }
            
            response = await self.client.get(url, headers=headers)
            response.raise_for_status()
            
            return response.json()['items']
        except Exception as e:
            logger.error(f"Failed to get unmapped items: {e}")
            return []
    
    async def save_mapping(self, mapping_data: MappingRequest) -> Dict[str, any]:
        """Save mapping between excise and Madhushala items"""
        try:
            url = f"{self.base_url}/api/excise-import/save-mapping?shopCode={self.shop_code}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            response = await self.client.post(url, json=mapping_data.dict(), headers=headers)
            response.raise_for_status()
            
            return response.json()
        except Exception as e:
            logger.error(f"Failed to save mapping: {e}")
            return {"error": str(e)}
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()