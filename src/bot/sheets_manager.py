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

class SheetsManager:
    """Manages Google Sheets operations with caching for multiple sheets"""
    
    def __init__(self, creds_file: str = 'creds.json', cache_duration: int = 300):
        """
        Initialize the sheets manager
        
        Args:
            creds_file: Path to service account credentials JSON file
            cache_duration: How long to cache sheet data (in seconds), default 5 minutes
            
        The manager supports multiple sheets with separate caching for each sheet.
        """
        self.creds_file = creds_file
        self.cache_duration = cache_duration
        self.service = None
        self.cache = {}  # Dictionary to store data from different sheets
        self.last_fetch = {}  # Track last fetch time per sheet
        
        # Try to initialize the service
        self._init_service()
        
    def reset_cache(self, sheet_id: Optional[str] = None) -> None:
        """
        Reset cached data and fetch timestamps
        
        Args:
            sheet_id: Optional specific sheet ID to reset. If None, resets all caches.
        """
        if sheet_id:
            self.cache.pop(sheet_id, None)
            self.cache.pop(f"{sheet_id}_events", None)
            self.last_fetch.pop(sheet_id, None)
            self.last_fetch.pop(f"{sheet_id}_events", None)
            logger.info(f"Cache reset for sheet {sheet_id}")
        else:
            self.cache.clear()
            self.last_fetch.clear()
            logger.info("All sheet caches reset successfully")
    
    def _init_service(self) -> None:
        """Initialize the Google Sheets service with credentials"""
        try:
            if not os.path.exists(self.creds_file):
                logger.warning(f"Credentials file not found: {self.creds_file} (Sheets features will be disabled)")
                return
            
            # Create credentials from the service account file
            scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
            credentials = service_account.Credentials.from_service_account_file(
                self.creds_file, scopes=scopes)
            
            # Build the service
            self.service = build('sheets', 'v4', credentials=credentials)
            logger.info("Successfully initialized Google Sheets service")
            
        except Exception as e:
            logger.error(f"Failed to initialize Sheets service: {e}")
            self.service = None
    
    def _is_cache_valid(self, sheet_id: str) -> bool:
        """
        Check if the cached data for a specific sheet is still valid
        
        Args:
            sheet_id: The ID of the Google Sheet to check
            
        Returns:
            bool: True if the cache is still valid for this sheet
        """
        last_fetch_time = self.last_fetch.get(sheet_id, 0)
        return (time.time() - last_fetch_time) < self.cache_duration
    
    async def get_alliance_data(self, spreadsheet_id: Optional[str] = None, range_name: str = 'Members!A2:I') -> List[Dict[str, Any]]:
        """
        Fetch alliance member data from Google Sheets
        
        Args:
            spreadsheet_id: The ID of the Google Sheet (optional, will use env var if not provided)
            range_name: The range to fetch from Members tab
        
        Returns:
            List of dictionaries containing member data
        """
        # Use environment variable if no spreadsheet_id provided
        if not spreadsheet_id:
            spreadsheet_id = os.getenv('GOOGLE_SHEET_ID')
            if not spreadsheet_id:
                logger.error("No Google Sheet ID provided and GOOGLE_SHEET_ID not set in environment")
                return []

        # Check cache first
        if self._is_cache_valid(spreadsheet_id) and spreadsheet_id in self.cache:
            logger.debug("Returning cached alliance data")
            return self.cache[spreadsheet_id]
        
        try:
            if not self.service:
                self._init_service()  # Try to reinitialize
                if not self.service:
                    raise Exception("Google Sheets service not initialized")
            
            # Fetch the data
            try:
                result = self.service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id,
                    range=range_name
                ).execute()
                
                # Process the rows
                rows = result.get('values', [])
                logger.info(f"Successfully fetched {len(rows)} rows from Members sheet")
            except Exception as e:
                logger.error(f"Failed to fetch data from Members sheet: {e}")
                return []
                
            if not rows:
                logger.warning("No data found in Members sheet")
                return []
            
            # Convert to list of dicts
            data = []
            for row in rows:
                # Pad row if needed (9 columns for full data)
                row_data = row + [''] * (9 - len(row))
                
                # Clean and validate the data
                alliance_name = row_data[0].strip() if row_data[0] else ''
                name = row_data[1].strip() if row_data[1] else ''
                player_id = row_data[2].strip() if row_data[2] else ''
                rank = row_data[3].strip().upper() if row_data[3] else ''
                power = row_data[4].strip() if row_data[4] else ''  # Adjusted column indices
                
                # Skip empty or invalid rows
                if not alliance_name or not name or not rank or rank not in ['R1', 'R2', 'R3', 'R4', 'R5']:
                    continue
                
                # Clean up alliance name (remove any extra spaces or case differences)
                alliance_name = alliance_name.upper().strip()
                
                # Create member dictionary
                member = {
                    'Alliance Name': alliance_name,
                    'Name': name,
                    'Player ID': player_id,
                    'Rank': rank,
                    'Power': power
                }
                data.append(member)
                
                # Process power value - handle empty or invalid values
                if power:
                    try:
                        # Remove any 'M' suffix and convert to float
                        power_clean = power.lower().replace('m', '').strip()
                        power_float = float(power_clean)
                        power = f"{power_float:.1f}M"
                    except ValueError:
                        power = ''
                
                # Only add row if we have the minimum required data
                if alliance_name and name and rank:
                    member = {
                        'Alliance Name': alliance_name,
                        'Name': name,
                        'Player ID': player_id,
                        'Rank': rank,
                        'Power': power
                    }
                    data.append(member)
                    
            # Update cache for this sheet
            self.cache[spreadsheet_id] = data
            self.last_fetch[spreadsheet_id] = time.time()
            logger.info(f"Successfully fetched {len(data)} alliance members")
            
            return data
            
        except HttpError as e:
            logger.error(f"HTTP error while fetching sheet data: {e}")
            # Return cached data if available, otherwise empty list
            return self.cache if self.cache else []
            
        except Exception as e:
            logger.error(f"Error fetching alliance data: {e}")
            return self.cache if self.cache else []
    
    async def get_event_guides(self, spreadsheet_id: Optional[str] = None, range_name: str = 'Event Guides!A2:D') -> List[Dict[str, Any]]:
        """
        Fetch event guides data from Google Sheets
        
        Args:
            spreadsheet_id: The ID of the Google Sheet (optional, will use env var if not provided)
            range_name: The range to fetch from Event Guides tab
        
        Returns:
            List of dictionaries containing event guides data
        """
        # Use environment variable if no spreadsheet_id provided
        if not spreadsheet_id:
            spreadsheet_id = os.getenv('GOOGLE_SHEET_ID')
            if not spreadsheet_id:
                logger.error("No Google Sheet ID provided and GOOGLE_SHEET_ID not set in environment")
                return []

        # Check cache first
        cache_key = f"{spreadsheet_id}_events"
        if self._is_cache_valid(cache_key) and cache_key in self.cache:
            logger.debug("Returning cached event guides data")
            return self.cache[cache_key]
        
        try:
            if not self.service:
                self._init_service()
                if not self.service:
                    raise Exception("Google Sheets service not initialized")
            
            # Fetch the data
            try:
                result = self.service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id,
                    range=range_name
                ).execute()
                
                rows = result.get('values', [])
                logger.info(f"Successfully fetched {len(rows)} rows from Event Guides sheet")
            except Exception as e:
                logger.error(f"Failed to fetch data from Event Guides sheet: {e}")
                return []
                
            if not rows:
                logger.warning("No data found in Event Guides sheet")
                return []
            
            # Convert to list of dicts
            data = []
            for row in rows:
                # Pad row if needed (4 columns expected)
                row_data = row + [''] * (4 - len(row))
                
                event_name = row_data[0].strip() if row_data[0] else ''
                description = row_data[1].strip() if row_data[1] else ''
                tips = row_data[2].strip() if row_data[2] else ''
                rewards = row_data[3].strip() if row_data[3] else ''
                
                # Only add row if we have the minimum required data
                if event_name and description:
                    guide = {
                        'Event Name': event_name,
                        'Description': description,
                        'Tips': tips,
                        'Rewards': rewards
                    }
                    data.append(guide)
            
            # Update cache for event guides
            self.cache[cache_key] = data
            self.last_fetch[cache_key] = time.time()
            logger.info(f"Successfully fetched {len(data)} event guides")
            
            return data
            
        except HttpError as e:
            logger.error(f"HTTP error while fetching event guides: {e}")
            return self.cache.get(cache_key, [])
            
        except Exception as e:
            logger.error(f"Error fetching event guides: {e}")
            return self.cache.get(cache_key, [])

    def format_event_guides_for_prompt(self, data: List[Dict[str, Any]], max_length: int = 2000) -> str:
        """
        Format event guides data for inclusion in the AI prompt
        
        Args:
            data: List of event guide dictionaries
            max_length: Maximum characters to include
            
        Returns:
            Formatted string for the prompt
        """
        if not data:
            return "No event guides available."
        
        # Convert to DataFrame for easier manipulation
        df = pd.DataFrame(data)
        
        formatted_text = "Available Event Guides:\n\n"
        for _, row in df.iterrows():
            event_text = f"Event: {row['Event Name']}\n"
            if row['Description']:
                event_text += f"Description: {row['Description']}\n"
            if row['Tips']:
                event_text += f"Tips: {row['Tips']}\n"
            if row['Rewards']:
                event_text += f"Rewards: {row['Rewards']}\n"
            event_text += "\n"
            
            # Check if adding this event would exceed max length
            if len(formatted_text) + len(event_text) > max_length:
                formatted_text += "...(truncated for length)"
                break
                
            formatted_text += event_text
            
        return formatted_text

    def format_alliance_data_for_prompt(self, data: List[Dict[str, Any]], max_length: int = 2000) -> str:
        """
        Format alliance data for inclusion in the AI prompt
        
        Args:
            data: List of member dictionaries
            max_length: Maximum characters to include
        
        Returns:
            When list is short: Formatted string
            When list is long: String starting with "ALLIANCE_MESSAGES:" followed by JSON array of messages
        """
        if not data:
            return "No alliance data available."
        
        # Convert to DataFrame for easier manipulation
        df = pd.DataFrame(data)
        
        # Create a summary section
        total_members = len(df)
        
        # Get rank counts with sorting to ensure consistent order
        ranks = df['Rank'].value_counts().sort_index()
        
        sections = []
        
        # Calculate average power if available
        power_text = ""
        if 'Power' in df.columns:
            def convert_power(x):
                if pd.isna(x) or x == '':
                    return None
                if isinstance(x, (int, float)):
                    return float(x)
                try:
                    # Remove 'M' and convert to float, ignoring empty strings
                    value = str(x).upper().replace('M', '').strip()
                    return float(value) if value else None
                except (ValueError, TypeError):
                    return None
            
            power_values = df['Power'].apply(convert_power)
            valid_powers = power_values[power_values.notna()]
            if not valid_powers.empty:
                avg_power = valid_powers.mean()
                power_text = f"\n• Average Power: {avg_power:.1f}M"
        
        # 1. Overall summary
            # Filter for ICE alliance members using case-insensitive match
            # Filter for ICE alliance members using case-insensitive match
            ice_members = df[df['Alliance Name'].str.upper() == 'ICE']
            ice_total = len(ice_members)
            
            if ice_total == 0:
                return "No ICE alliance members found. Please make sure the alliance name is correctly set in the sheet."
        
        messages = []
        current_message = []
        
        # Start with summary
        current_message = [
            "📊 ICE ALLIANCE MEMBERS:",
            f"Total Members: {ice_total}\n"
        ]
        
        # Add rank distribution
        ice_ranks = ice_members['Rank'].value_counts().sort_index()
        current_message.append("Rank Distribution:")
        for rank, count in ice_ranks.items():
            current_message.append(f"- {rank}: {count} members")
        current_message.append("")  # Empty line after summary
        
        # Convert summary to first message
        messages.append("\n".join(current_message))
        # Process members by rank with type prefix
        for rank in ['R5', 'R4', 'R3', 'R2', 'R1']:
            rank_members = ice_members[ice_members['Rank'] == rank].sort_values('Name')
            if not rank_members.empty:
                current_message = []
                # Use different emoji for leadership vs regular ranks
                prefix = "👑" if rank in ['R5', 'R4'] else "👥"
                current_message.append(f"\n{prefix} {rank} MEMBERS:")
                
                for _, member in rank_members.iterrows():
                    name = member['Name']
                    player_id = str(member['Player ID']).strip()
                    power_str = f" | {member['Power']}" if member['Power'] else ""
                    player_id_str = f" | ID: {player_id}" if player_id else ""
                    member_line = f"• {name}{power_str}{player_id_str}"
                    
                    # Check if adding this line would exceed Discord's limit
                    if len("\n".join(current_message + [member_line])) > 1900:  # Leave some margin
                        messages.append("\n".join(current_message))
                        current_message = [f"👑 {rank} MEMBERS (Continued):"]
                    
                    current_message.append(member_line)
                
                if current_message:
                    messages.append("\n".join(current_message))
        
        # If we have multiple messages, return them in the special format
        if len(messages) > 1:
            return "ALLIANCE_MESSAGES:" + json.dumps(messages)
        elif messages:
            return messages[0]
        else:
            return "No ICE alliance members found."