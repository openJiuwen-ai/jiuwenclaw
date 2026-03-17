# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any
import requests
from pydantic import BaseModel, Field
from openjiuwen.core.common.logging import context_engine_logger as logger

from openjiuwen.core.foundation.tool import ToolCard, LocalFunction


class WikipediaSearchParams(BaseModel):
    query: str = Field(..., description="The search query for Wikipedia")


def search_wikipedia(query: str) -> str:
    """
    Search Wikipedia for the given query and return the summary of the top result.
    """
    logger.info("Searching Wikipedia for: %s", query)
    
    url = "https://en.wikipedia.org/w/api.php"
    # Wikipedia requires a User-Agent to identify the client
    headers = {
        "User-Agent": "OpenJiuwenAgent/1.0 (Educational Research)"
    }
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": query,
        "srlimit": 1
    }
    
    try:
        # First, search for the page
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        search_results = response.json().get("query", {}).get("search", [])
        
        if not search_results:
            return f"No Wikipedia results found for '{query}'."
        
        # Get the pageid of the top result
        page_id = search_results[0]["pageid"]
        
        # Now fetch the summary/extract for that page
        summary_params = {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "pageids": page_id,
            "explaintext": True,
            "exintro": True,
            "exlimit": 1
        }
        
        summary_response = requests.get(url, params=summary_params, headers=headers)
        summary_response.raise_for_status()
        
        pages = summary_response.json().get("query", {}).get("pages", {})
        page_data = pages.get(str(page_id), {})
        extract = page_data.get("extract", "")
        
        if not extract:
            return f"Found page '{search_results[0]['title']}' for '{query}', but no summary available."
            
        result = f"Title: {search_results[0]['title']}\nSummary: {extract}"
        # Truncate if too long (simple check)
        if len(result) > 2000:
            result = result[:2000] + "..."
            
        return result
        
    except Exception as e:
        logger.error("Wikipedia search failed: %s", e)
        return f"Error searching Wikipedia: {str(e)}"

# Define the ToolCard
wikipedia_tool_card = ToolCard(
    name="wikipedia_search",
    description="Search Wikipedia for information about a topic.",
    input_params=WikipediaSearchParams.model_json_schema()
)

# Create the LocalFunction tool
wikipedia_tool = LocalFunction(
    card=wikipedia_tool_card,
    func=search_wikipedia
)
