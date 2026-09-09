import requests
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup

# Configuration for targets
GAMES = {
    "HSR": {
        "reddit": "HonkaiStarRail",
        "wiki_url": "https://honkai-star-rail.fandom.com/api.php",
        "wiki_page": "Redemption_Code"
    },
    "ZZZ": {
        "reddit": "ZenlessZoneZero",
        "wiki_url": "https://zenless-zone-zero.fandom.com/api.php",
        "wiki_page": "Redemption_Code"
    },
    "WUWA": {
        "reddit": "WutheringWaves",
        "wiki_url": "https://wutheringwaves.fandom.com/api.php",
        "wiki_page": "Redemption_Code"
    }
}

# A custom User-Agent is strictly required by Reddit to avoid HTTP 429 (Too Many Requests) errors.
HEADERS = {
    "User-Agent": "python:gacha_tracker_bot:v1.0 (by /u/your_reddit_username)"
}

# Regex to identify typical gacha codes (Uppercase alphanumeric, 6 to 16 characters)
CODE_REGEX = re.compile(r'\b[A-Z0-9]{6,16}\b')

def get_redeem_link(game, code):
    """Generates the 1-click URL based on the game."""
    if game == "HSR":
        return f"https://hsr.hoyoverse.com/gift?code={code}"
    elif game == "ZZZ":
        return f"https://zenless.hoyoverse.com/redemption?code={code}"
    return "In-game only (Settings > Other Settings)"

def fetch_reddit_codes(game_id, subreddit):
    """Scrapes the latest posts from a subreddit looking for codes."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
    found_codes = []
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        posts = response.json().get("data", {}).get("children", [])
        
        for post in posts:
            data = post["data"]
            title = data.get("title", "")
            text = data.get("selftext", "")
            
            # Filter posts that are likely about codes or livestreams
            if any(keyword in title.lower() for keyword in ["code", "redeem", "livestream", "gem"]):
                matches = CODE_REGEX.findall(title) + CODE_REGEX.findall(text)
                for match in set(matches):
                    # Ignore common false positive acronyms
                    if match not in ["LIVESTREAM", "UPDATE", "REDDIT"]:
                        found_codes.append({
                            "game": game_id,
                            "code": match,
                            "source": f"Reddit (https://reddit.com{data.get('permalink')})"
                        })
    except Exception as e:
        print(f"[!] Reddit fetch failed for {game_id}: {e}")
        
    return found_codes

def fetch_wiki_codes(game_id, api_url, page_title):
    """Fetches HTML from the Fandom MediaWiki API and extracts codes."""
    found_codes = []
    params = {
        "action": "parse",
        "page": page_title,
        "format": "json",
        "prop": "text"
    }
    
    try:
        response = requests.get(api_url, params=params, headers=HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        html_content = data.get("parse", {}).get("text", {}).get("*", "")
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Fandom wikis typically place codes in <code> tags or table cells (<td>)
        # We will parse <code> blocks as they are the most reliable indicator of a string meant to be copied.
        for code_tag in soup.find_all("code"):
            code_text = code_tag.get_text(strip=True).upper()
            if CODE_REGEX.match(code_text):
                found_codes.append({
                    "game": game_id,
                    "code": code_text,
                    "source": "Fandom Wiki API"
                })
                
    except Exception as e:
        print(f"[!] Wiki fetch failed for {game_id}: {e}")
        
    return found_codes

def run_pipeline():
    """Executes the ingestion engine, deduplicates, and formats the output."""
    print(f"--- Starting Scrape Pipeline at {datetime.now().isoformat()} ---")
    master_list = []
    
    for game, config in GAMES.items():
        print(f"Fetching data for {game}...")
        reddit_codes = fetch_reddit_codes(game, config["reddit"])
        wiki_codes = fetch_wiki_codes(game, config["wiki_url"], config["wiki_page"])
        
        master_list.extend(reddit_codes)
        master_list.extend(wiki_codes)
        
    # Deduplication Engine (FR-2)
    # We use a dictionary keyed by (game, code) to ensure we only keep unique codes
    unique_codes = {}
    for item in master_list:
        key = (item["game"], item["code"])
        if key not in unique_codes:
            unique_codes[key] = {
                "game": item["game"],
                "code": item["code"],
                "source_url": item["source"],
                "direct_redeem_url": get_redeem_link(item["game"], item["code"]),
                "discovered_at": datetime.now().isoformat(),
                "status": "ACTIVE"
            }
            
    # Convert back to a list for JSON output or database insertion
    final_output = list(unique_codes.values())
    
    print(f"Pipeline complete. Discovered {len(final_output)} unique codes.")
    
    # ---------------------------------------------------------
    # TODO (Phase 2): Insert `final_output` into PostgreSQL / Supabase here
    # Example: supabase.table('game_codes').upsert(final_output).execute()
    # ---------------------------------------------------------
    
    # Output to console for testing
    print(json.dumps(final_output, indent=2))

if __name__ == "__main__":
    run_pipeline()s