"""
Google Sheets Manager for Alliance Data and Event Guides

This module handles fetching and caching data from Google Sheets 
containing alliance member information and event guides for Whiteout Survival game.
"""

import os
import json
import time
import re
from typing import List, Dict, Any, Optional, Union
import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import logging

logger = logging.getLogger(__name__)

class SheetsManager:
    """Manages Google Sheets operations with caching for multiple sheets"""
    
    def __init__(self, creds_file: str = 'creds.json', cache_duration: int = 300):
        """
        Initialize the sheets manager
        
        Args:
            creds_file: Path to service account credentials JSON file
            cache_duration: How long to cache sheet data (in seconds), default 5 minutes
        """
        self.creds_file = creds_file
        self.service = None
        self.cache = {}  # Dictionary to store data from different sheets
        self.last_fetch = {}  # Track last fetch time per sheet
        self.cache_duration = cache_duration
        self._init_service()

    def reset_cache(self):
        """Reset all cached data and fetch timestamps"""
        self.cache = {}
        self.last_fetch = {}
        logger.info("Sheet cache reset successfully")

    def _init_service(self):
        """Initialize the Google Sheets service with credentials"""
        try:
            if not os.path.exists(self.creds_file):
                logger.error(f"Credentials file not found: {self.creds_file}")
                return

            creds = service_account.Credentials.from_service_account_file(
                self.creds_file,
                scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
            )
            self.service = build('sheets', 'v4', credentials=creds)
            logger.info("Google Sheets service initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Sheets service: {e}", exc_info=True)

    def _is_cache_valid(self, sheet_name: str) -> bool:
        """
        Check if cached data for a specific sheet is still valid
        
        Args:
            sheet_name: Name of the sheet to check
            
        Returns:
            bool: True if cache is valid, False if expired or not found
        """
        if sheet_name not in self.last_fetch:
            return False
        return (time.time() - self.last_fetch[sheet_name]) < self.cache_duration

    async def fetch_sheet_data(self, spreadsheet_id: str, sheet_name: str = 'Members') -> List[Dict[str, Any]]:
        """
        Fetch data from a specific sheet in the spreadsheet
        
        Args:
            spreadsheet_id: The ID of the Google Sheet
            sheet_name: Name of the sheet to fetch (Members or Event Guides)
            
        Returns:
            List of dictionaries containing the sheet data
        """
        # Check cache first
        if self._is_cache_valid(sheet_name) and sheet_name in self.cache:
            logger.debug(f"Returning cached {sheet_name} data")
            return self.cache[sheet_name]
        
        try:
            if not self.service:
                self._init_service()
                if not self.service:
                    logger.error("Google Sheets service not initialized")
                    return []
            
            # Get the sheet data
            result = self.service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_name}!A:Z"  # Get all columns
            ).execute()
            
            rows = result.get('values', [])
            if not rows:
                logger.warning(f"No data found in sheet: {sheet_name}")
                return []
            
            # Get headers from first row
            headers = [h.strip() for h in rows[0]]
            data = []
            
            # Process rows based on sheet type
            for row in rows[1:]:  # Skip header row
                # Pad row if needed
                row_data = row + [''] * (len(headers) - len(row))
                
                # Convert row to dictionary
                row_dict = {}
                for i, header in enumerate(headers):
                    value = row_data[i].strip() if i < len(row_data) else ''
                    
                    # Special processing for Members sheet
                    if sheet_name == 'Members':
                        if header == 'Active':
                            value = value.lower() in ['yes', 'true', '1', 'active']
                        elif header == 'State':
                            value = '3063'  # Always set state to 3063
                        elif header == 'Rank':
                            value = value.upper() if value else ''
                    
                    row_dict[header] = value
                
                # Validate row based on sheet type
                if sheet_name == 'Members':
                    if not row_dict.get('Name') or not row_dict.get('Rank') or \
                       row_dict['Rank'] not in ['R1', 'R2', 'R3', 'R4', 'R5']:
                        continue
                else:  # Event Guides
                    if not row_dict.get('Event Name') or not row_dict.get('Description'):
                        continue
                
                data.append(row_dict)
            
            # Update cache
            self.cache[sheet_name] = data
            self.last_fetch[sheet_name] = time.time()
            
            logger.info(f"Successfully fetched {len(data)} records from {sheet_name}")
            return data
            
        except HttpError as e:
            logger.error(f"HTTP error while fetching {sheet_name} data: {e}")
            return self.cache.get(sheet_name, [])
            
        except Exception as e:
            logger.error(f"Error fetching {sheet_name} data: {e}", exc_info=True)
            return self.cache.get(sheet_name, [])

    def format_sheet_data_for_prompt(self, data: List[Dict[str, Any]], sheet_name: str) -> str:
        """
        Format sheet data for inclusion in the AI prompt
        
        Args:
            data: List of dictionaries containing sheet data
            sheet_name: Name of the sheet being formatted
            
        Returns:
            Formatted string for the prompt
        """
        if not data:
            return f"No {sheet_name.lower()} data available."
        
        if sheet_name == 'Members':
            # Convert to DataFrame for easier manipulation
            df = pd.DataFrame(data)
            
            # Create a summary section
            total_members = len(df)
            active_members = df['Active'].sum() if 'Active' in df else 0
            inactive_members = total_members - active_members
            
            # Get rank distribution
            ranks = df['Rank'].value_counts().sort_index()
            
            sections = []
            
            # 1. Overall summary
            summary = [
                "📊 ALLIANCE STATISTICS:",
                f"• Total Members: {total_members}",
                f"• Active Members: {active_members}",
                f"• Inactive Members: {inactive_members}",
                "• Rank Distribution:"
            ]
            
            for rank, count in ranks.items():
                active_in_rank = len(df[(df['Rank'] == rank) & (df['Active'])])
                summary.append(f"  - {rank}: {count} members ({active_in_rank} active)")
            
            sections.append("\n".join(summary))
            
            return "\n\n".join(sections)
            
        else:  # Event Guides
            # Format event guides
            sections = ["📖 EVENT GUIDES:"]
            
            for event in data:
                sections.append(
                    f"• {event.get('Event Name', 'Unknown Event')}\n" +
                    f"  {event.get('Description', 'No description available')}\n" +
                    (f"  Tips: {event.get('Tips', 'No tips available')}" if event.get('Tips') else "")
                )
            
            return "\n\n".join(sections)

def is_event_related_query(question: str) -> bool:
    """
    Determine if a question is related to events
    
    Args:
        question: User's question
        
    Returns:
        bool: True if the question is about events
    """
    event_keywords = [
        'event', 'guide', 'rewards', 'tips', 'strategy', 'how to',
        'what is', 'explain', 'help with', 'about the'
    ]
    
    question = question.lower()
    return any(keyword in question for keyword in event_keywords)