"""
Alliance Data Filter Module

This module provides functions for filtering and formatting alliance data
based on user queries.
"""

from typing import List, Dict, Any, Optional, Iterable
import re
from datetime import datetime
import bot_config

def is_alliance_related(question: str, sheet_data: List[Dict[str, Any]] = None) -> bool:
    """
    Determine if a question is specifically related to alliance information.
    
    Args:
        question: The user's question
        sheet_data: List of alliance member data to check against
        
    Returns:
        bool: True if the question is about alliance members or data
    """
    # Convert to lowercase for case-insensitive matching
    q = question.lower()
    
    # Keywords that indicate general alliance list queries
    list_keywords = [
        'list all', 'show all', 'display all', 
        'alliance members', 'all members',
        'who are our', 'who is in', 'who is',
        'list of all members', 'list of', 'show me',
        'tell me', 'give me', 'what is', 'what are',
        'find', 'search', 'lookup', 'get'
    ]
    
    # Keywords for different data types
    member_keywords = [
        'r5', 'r4', 'r3', 'r2', 'r1', 
        'player id', 'id', 'playerid', 'pid',
        'member', 'player', 'person', 'user',
        'leader', 'officer', 'member'
    ]
    
    alliance_keywords = ['ice', 'kor', 'gtacat', 'caa', 'kmb']
    
    power_keywords = [
        'power', 'power level', 'strongest', 'weakest',
        'powerful', 'strength', 'strong', 'weak',
        'highest power', 'lowest power', 'top power',
        'most powerful', 'least powerful'
    ]
    
    state_keywords = ['state', 'state 3063', '3063', 'location']
    
    info_keywords = [
        'info', 'information', 'details', 'stats',
        'status', 'profile', 'data', 'about'
    ]
    
    # Check for alliance name + rank combinations first
    for alliance in alliance_keywords:
        for rank in member_keywords:
            if f"{rank}" in q and f"{alliance}" in q:
                return True
    
    # First check if this is a specific member query
    if sheet_data:
        query_words = set(q.split())
        for member in sheet_data:
            name = member.get('Name', '').lower()
            # Exact match first
            if name == q or name in q:
                return True
            # Then try partial match with name parts
            name_parts = set(name.split())
            if name_parts and name_parts.issubset(query_words):
                return True
    
    # Check for direct alliance mentions
    if any(alliance in q for alliance in alliance_keywords):
        if any(keyword in q for keyword in list_keywords + member_keywords + power_keywords):
            return True
            
    # Check for list queries (should return full or filtered lists)
    if any(keyword in q for keyword in list_keywords):
        # If there's a rank or alliance keyword in the query
        if any(keyword in q for keyword in member_keywords + alliance_keywords):
            return True
        
    # Check for specific rank queries with alliance context
    if any(keyword in q for keyword in member_keywords):
        if any(alliance in q for alliance in alliance_keywords):
            return True
            
    # Check for alliance-specific queries
    if any(alliance in q for alliance in alliance_keywords):
        return True
    
    return False

def filter_sheet_data(question: str, sheet_data: List[Dict[str, Any]], max_rows: int = None) -> List[Dict[str, Any]]:
    """
    Filter alliance data based on user's question using keyword detection.
    
    Args:
        question: The user's question or query
        sheet_data: List of dictionaries containing alliance member data
        max_rows: Maximum number of rows to return (None for unlimited)
        
    Returns:
        Filtered list of member data dictionaries
    """
    # Check if this is alliance related based on the question and available data
    if not sheet_data or not is_alliance_related(question, sheet_data):
        return []
        
    # Convert question to lowercase for case-insensitive matching
    q = question.lower()
    # Normalized query (alphanumeric only) for fuzzy name matching
    def _normalize_text(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', (s or '').lower())
    normalized_q = _normalize_text(q)
    filtered_data = []
    
    # Check for specific member name in question
    query_words = set(q.split())  # Convert query to set of words for faster lookups
    
    # Remove common words that might interfere with name matching
    stop_words = {'the', 'of', 'in', 'for', 'to', 'what', 'who', 'is', 'are', 'can', 'you', 'tell', 'me', 'about', 'show', 'list'}
    query_words = query_words - stop_words
    
    for member in sheet_data:
        name = member.get('Name', '') or ''
        name_lower = name.lower()
        normalized_name = _normalize_text(name_lower)

        # Try normalized exact match first (handles special characters)
        if normalized_name and normalized_name == normalized_q:
            return [member]

        # Try plain exact or substring matches
        if name_lower == q or name_lower in q:
            return [member]

        # Try name as a single unit within query words
        if normalized_name and normalized_name in ''.join(sorted(query_words)):
            return [member]

        # Try matching individual parts
        name_parts = [p for p in re.split(r"\s+", name_lower) if p]
        name_parts_normalized = set(_normalize_text(p) for p in name_parts if p)

        # Check for full normalized name match (all parts present)
        if name_parts_normalized and name_parts_normalized.issubset(set(_normalize_text(w) for w in query_words)):
            return [member]

        # Check for partial matches with high confidence (>=75% of parts)
        matching_parts = name_parts_normalized.intersection(set(_normalize_text(w) for w in query_words))
        if name_parts_normalized and len(matching_parts) >= max(1, int(len(name_parts_normalized) * 0.75)):
            return [member]
    
    # If no specific member was found, check if this is a request about all members
    
    # Parse query for filters - initialize single filters dictionary
    filters = {
        'alliance': None,
        'rank': None,
        'state': None,
        'power_comparison': None,
        'active': None,
        'date': None
    }
    
    # Check for alliance name - exact match with word boundaries or at start/end
    for alliance in ['ICE', 'KOR', 'GTACAT', 'CAA', 'KMB']:
        # Check for alliance name at start, end, or surrounded by spaces
        pattern = f"(^{alliance.lower()}\\b|\\b{alliance.lower()}$|\\s{alliance.lower()}\\s)"
        if re.search(pattern, q):
            filters['alliance'] = alliance
            break
    
    # Check for rank - exact match with word boundaries
    for rank in ['r5', 'r4', 'r3', 'r2', 'r1']:
        pattern = f"(^{rank}\\b|\\b{rank}$|\\s{rank}\\s)"
        if re.search(pattern, q):
            filters['rank'] = rank.upper()
            break
    
    # Check for state
    if 'state' in q or '3063' in q:
        filters['state'] = '3063'
    
    # Check for power comparison
    power_high_indicators = ['strongest', 'highest', 'most powerful', 'top', 'best']
    power_low_indicators = ['weakest', 'lowest', 'least powerful', 'bottom', 'minimal']
    
    if any(indicator in q for indicator in power_high_indicators):
        filters['power_comparison'] = 'highest'
    elif any(indicator in q for indicator in power_low_indicators):
        filters['power_comparison'] = 'lowest'
        
    # Extract number of results if specified (e.g., "top 5", "strongest 10")
    number_match = re.search(r'(?:top|bottom|strongest|weakest|highest|lowest)\s+(\d+)', q)
    if number_match:
        filters['limit'] = int(number_match.group(1))
    
    # Initialize filtered data and apply all filters at once
    seen_ids = set()
    filtered_data = []
    
    for x in sheet_data:
        alliance_name = x.get('Alliance Name', '').upper()
        player_id = x.get('Player ID')
        rank = x.get('Rank', '').upper()
        
        # Skip if we already have this player
        if player_id in seen_ids:
            continue
            
        # Apply all filters at once
        if filters['alliance'] and alliance_name != filters['alliance']:
            continue
            
        if filters['rank'] and rank != filters['rank']:
            continue
            
        if filters['state'] and x.get('STATE 3063') != filters['state']:
            continue
        
        seen_ids.add(player_id)
        filtered_data.append(x)
    
    # Check for active/inactive status
    if 'active' in q:
        filters['active'] = True
    elif 'inactive' in q:
        filters['active'] = False
    
    # Check for active/inactive filter
    if 'active' in q:
        filters['active'] = True
    elif 'inactive' in q:
        filters['active'] = False
    
    # Apply filters
    if filters['rank']:
        filtered_data = [x for x in filtered_data if x.get('Rank', '').upper() == filters['rank']]
    if filters['active'] is not None:
        filtered_data = [x for x in filtered_data if x.get('Active', False) == filters['active']]
    if filters['state']:
        filtered_data = [x for x in filtered_data if x.get('State', '').lower() == filters['state'].lower()]
    
    # Sort results
    def get_power_value(x):
        try:
            power = x.get('Power', '0').replace(',', '').replace('M', '')
            return float(power)
        except (ValueError, TypeError):
            return 0

    if filters.get('power_comparison') == 'highest':
        filtered_data.sort(key=lambda x: (-get_power_value(x), x.get('Name', '').lower()))
    elif filters.get('power_comparison') == 'lowest':
        filtered_data.sort(key=lambda x: (get_power_value(x), x.get('Name', '').lower()))
    else:
        # Default sort by rank and name
        filtered_data.sort(key=lambda x: (
            'R54321'.index(x.get('Rank', 'R9')[-1]) if x.get('Rank', 'R9')[-1] in '54321' else 9,
            x.get('Name', '').lower()
        ))
    
    # If no specific filters were applied and the dataset is large,
    # default to showing only active members
    if not any(filters.values()) and len(filtered_data) > 50:
        filtered_data = [x for x in filtered_data if x.get('Active', False)]
        
    # Return all results or up to max_rows if specified
    return filtered_data[:max_rows] if max_rows is not None else filtered_data

def format_alliance_data(data: List[Dict[str, Any]], query: str = '', max_display: int = None, allow_player_ids: Optional[bool] = None, caller_roles: Optional[Iterable[str]] = None) -> List[str]:
    """Format filtered alliance data into a concise text summary.

    Parameters added:
    - allow_player_ids: if set (True/False) it overrides the global config for this call.
    - caller_roles: optional iterable of caller roles (e.g. ['admin']) that may be used
      by the authorization helper to permit IDs for trusted users.

    Args:
        data: Filtered list of member dictionaries
        query: Original query for context-aware formatting
        max_display: Maximum number of members to display in detail (None for unlimited)

    Returns:
        List of formatted message strings, each under 2000 characters
    """

    # Query keywords for response formatting
    power_keywords = [
        'power', 'power level', 'strongest', 'weakest',
        'powerful', 'strength', 'strong', 'weak'
    ]

    info_keywords = [
        'info', 'information', 'details', 'stats',
        'status', 'profile', 'data', 'about'
    ]
    if not data:
        return ["No alliance members found matching your query."]

    # Determine whether to include player IDs in responses
    allow_ids = bot_config.can_show_player_ids(caller_roles) if allow_player_ids is None else bool(allow_player_ids)

    # Convert query to lowercase for matching
    q = query.lower()
    # Identify id-related queries early so we don't fall back to other fields
    # Use word-boundary regex so 'id' inside other words isn't matched erroneously
    is_id_query = bool(re.search(r"\b(game\s+id|player\s+id|playerid|pid|\bid\b)", q))

    if is_id_query:
        # If IDs are not allowed, return an explicit refusal instead of falling back
        if not allow_ids:
            return [
                "I can't share player IDs due to privacy/configuration."
                " If you are an admin you can enable them by setting ALLOW_PLAYER_IDS"
                " or by passing allow_player_ids=True to the formatter."
            ]

        # IDs are allowed: return concise ID answers
        if len(data) == 1:
            member = data[0]
            return [f"Player ID for **{member.get('Name', 'Unknown')}** is: {member.get('Player ID', 'Unknown')}"]

        # Multiple members: return a simple list of names with IDs
        id_lines = []
        for member in data:
            name = member.get('Name', 'Unknown')
            pid = member.get('Player ID', 'Unknown')
            id_lines.append(f"• {name} | ID: {pid}")
        return id_lines[:max_display] if max_display is not None else id_lines
    
    # Check if this is a query about a specific member
    if len(data) == 1:
        member = data[0]
        name = member.get('Name', 'Unknown')
        rank = member.get('Rank', 'Unknown')
        alliance = member.get('Alliance Name', 'the alliance')
        power = member.get('Power', 'unknown')
        player_id = member.get('Player ID', 'Unknown')
        is_active = member.get('Active', False)
        state = member.get('State', '3063')  # Default to 3063 if not set

        # Build comprehensive response based on what was asked
        response_parts = []
        
        # Start with name and basic info
        response_parts.append(f"**{name}**")
        
        # Always include alliance and rank as basic context
        response_parts.append(f"{alliance} | {rank}")
        
        # Add specific information based on query
        if allow_ids and ('id' in q or 'player id' in q or 'pid' in q):
            response_parts.append(f"Player ID: {player_id}")
            
        if 'power' in q or any(word in q for word in power_keywords):
            response_parts.append(f"Power: {power}")
            
        if 'state' in q or '3063' in q or 'location' in q:
            state = member.get('STATE 3063', 'Unknown')
            response_parts.append(f"State: {state}")
            
        # Add activity status if specifically asked or for complete info
        if 'active' in q or 'status' in q or 'info' in q:
            status = "Active" if is_active else "Inactive"
            response_parts.append(f"Status: {status}")
            
        # If it's a general info query, include everything
        if any(word in q for word in info_keywords) or 'about' in q:
            if 'Power' not in ' '.join(response_parts):
                response_parts.append(f"Power: {power}")
            if 'State' not in ' '.join(response_parts):
                response_parts.append(f"State: {state}")
            if 'Status' not in ' '.join(response_parts):
                status = "Active" if is_active else "Inactive"
                response_parts.append(f"Status: {status}")
            
        # Only append ID if allowed by configuration
        if allow_ids and 'Player ID' not in ' '.join(response_parts):
            response_parts.append(f"ID: {player_id}")
            
        return [" | ".join(response_parts)]
        return [f"**{member.get('Name', 'Unknown')}**'s Player ID is: {member.get('Player ID', 'Unknown')}"]

    # If asking for a specific player's ID
    if ('id' in q or 'player id' in q) and len(data) == 1:
        member = data[0]
        return [f"Player ID for **{member.get('Name', 'Unknown')}** is: {member.get('Player ID', 'Unknown')}"]

    # First deduplicate the data based on Player ID
    seen_ids = set()
    unique_data = []
    for member in data:
        player_id = member.get('Player ID')
        if player_id and player_id not in seen_ids:
            seen_ids.add(player_id)
            unique_data.append(member)
    
    # Use the deduplicated data from here on
    data = unique_data
    
    # Get alliance name if all members are from the same alliance
    alliance_name = None
    if len(set(x.get('Alliance Name', '') for x in data)) == 1:
        alliance_name = data[0].get('Alliance Name')
    
    # Create messages list to store our response parts
    messages = []
    
    # Create summary header
    header_parts = []
    if alliance_name:
        header_parts.append(f"📊 **{alliance_name} Alliance Members List**")
    header_parts.append(f"Total: {len(data)} members")
    header_parts.append("\n*All member information including IDs is publicly available in game*")
    
    # Create rank distribution summary
    rank_counts = {}
    active_counts = {}
    for member in data:
        rank = member.get('Rank', 'Unknown')
        rank_counts[rank] = rank_counts.get(rank, 0) + 1
        if member.get('Active'):
            active_counts[rank] = active_counts.get(rank, 0) + 1
    
    # Add rank and activity statistics
    if rank_counts:
        rank_summary = []
        for rank in ['R5', 'R4', 'R3', 'R2', 'R1']:
            if rank in rank_counts:
                total = rank_counts[rank]
                active = active_counts.get(rank, 0)
                rank_summary.append(f"{rank}: {total} ({active} active)")
        if rank_summary:
            header_lines = [
                " ".join(header_parts),
                "",
                "**Rank Distribution:**",
                ", ".join(rank_summary),
                "",
                "**Member Details:**"
            ]
    else:
        header_lines = [" ".join(header_parts), ""]
    
    # Add the header as the first message
    messages.append("\n".join(header_lines))
    
    # Group members by rank for organized display
    current_rank = None
    current_message = []
    message_length = len(messages[0])
    
    # Sort members by rank first - data is already deduplicated
    sorted_data = sorted(data, key=lambda x: (
        'R54321'.index(x.get('Rank', 'R9')[-1]) if x.get('Rank', 'R9')[-1] in '54321' else 9,
        x.get('Name', '').lower()
    ))
    
    sorted_data = sorted(unique_data, key=lambda x: (
        'R54321'.index(x.get('Rank', 'R9')[-1]) if x.get('Rank', 'R9')[-1] in '54321' else 9,
        x.get('Name', '').lower()
    ))
    
    for member in sorted_data:
        # Format member details
        parts = [
            f"**{member.get('Name', 'Unknown')}**",
            f"{member.get('Rank', 'Unknown')}"
        ]
        
        # Include power only when specifically requested
        player_id = member.get('Player ID', 'Not Set')
        if isinstance(player_id, str):
            player_id = player_id.strip()
        
        # Only show power if it's specifically requested
        if 'power' in q.lower():
            power = member.get('Power', '').strip()
            if power:
                parts.append(power)
        
        # Only include ID in list output when allowed
        if allow_ids:
            parts.append(f"ID: {player_id}")
            
        member_line = "• " + " | ".join(parts)
        
        # Check if we need to start a new message due to length
        if message_length + len(member_line) + 2 > 1800:  # Increased buffer for page numbers
            if current_message:
                # Add current rank distribution for this page
                rank_info = []
                for rank in ['R5', 'R4', 'R3', 'R2', 'R1']:
                    members_in_rank = [m for m in current_message if rank in m]
                    if members_in_rank:
                        rank_info.append(f"{rank}: {len(members_in_rank)}")
                if rank_info:
                    current_message.insert(1, "**Current Page Ranks:** " + ", ".join(rank_info) + "\n")
                
                messages.append("\n".join(current_message))
            current_message = []
            message_length = 0
            
            # Add continuation header
            if current_rank:
                current_message.append(f"\n**{current_rank} Members (Continued):**")
                message_length += len(current_message[-1]) + 1
        
        # Add rank header if rank changed
        member_rank = member.get('Rank', 'Unknown')
        if member_rank != current_rank:
            current_rank = member_rank
            rank_header = f"\n**{current_rank} Members:**"
            current_message.append(rank_header)
            message_length += len(rank_header) + 1
        
        # Add member line
        current_message.append(member_line)
        message_length += len(member_line) + 1

        # If max_display is set and we've reached it, add a note about truncation
        if max_display and len(current_message) > max_display + 1:  # +1 for the rank header
            current_message.append(f"\n*Showing {max_display} of {len(data)} members. Please refine your search to see more.*")
            break
    
    # Add the last message if there's anything pending
    if current_message:
        messages.append("\n".join(current_message))

    # Add page numbers if there are multiple messages
    if len(messages) > 1:
        for i in range(len(messages)):
            messages[i] = f"{messages[i]}\n\n*Page {i+1} of {len(messages)}*"
    
    return messages