"""
Gacha Code Tracker - Phase 1: Scraping & Ingestion Engine
Implements FR-1 (multi-source polling), FR-2 (dedup), FR-4 (classification)
against the `game_codes` schema defined in the PDR (Section 5).

Requires:
    pip install requests beautifulsoup4

Environment variables (set these before running, see SETUP_INSTRUCTIONS.md):
    SUPABASE_URL   - e.g. https://xxxx.supabase.co
    SUPABASE_KEY   - service_role key (server-side only, never expose client-side)
"""

import os
import re
import json
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

GAMES = {
    "HSR": {
        "reddit": "HonkaiStarRail",
        "wiki_url": "https://honkai-star-rail.fandom.com/api.php",
        "wiki_page": "Redemption_Code",
    },
    "ZZZ": {
        "reddit": "ZenlessZoneZero",
        "wiki_url": "https://zenless-zone-zero.fandom.com/api.php",
        "wiki_page": "Redemption_Code",
    },
    "WUWA": {
        "reddit": "WutheringWaves",
        "wiki_url": "https://wutheringwaves.fandom.com/api.php",
        "wiki_page": "Redemption_Code",
    },
}

# Reddit requires a descriptive User-Agent or it will 429 you.
HEADERS = {
    "User-Agent": "python:gacha_tracker_bot:v1.0 (by /u/Own_Conversation9224)"
}

# Uppercase alphanumeric, 6-16 chars - typical gacha code shape.
CODE_REGEX = re.compile(r"\b[A-Z0-9]{6,16}\b")

# Words that match the regex shape but are never actual codes.
FALSE_POSITIVES = {"LIVESTREAM", "UPDATE", "REDDIT", "YOUTUBE", "TWITTER", "OFFICIAL"}

# Permanent starter codes called out explicitly in the PDR (Section 2, Returning Player persona).
# These are seeded once and should never trigger email alerts (FR-10).
PERMANENT_CODES = {
    "HSR": ["STARRAILGIFT"],
    "WUWA": ["WUTHERINGGIFT"],
    "ZZZ": ["ZENLESSGIFT"],
}

# Reward-keyword patterns used for best-effort reward extraction from post text.
REWARD_PATTERNS = [
    re.compile(r"\d[\d,]*\s*(?:Stellar\s*Jades?)", re.I),
    re.compile(r"\d[\d,]*\s*(?:Polychromes?)", re.I),
    re.compile(r"\d[\d,]*\s*(?:Astrites?)", re.I),
    re.compile(r"\d[\d,]*\s*(?:Credits?)", re.I),
    re.compile(r"\d[\d,]*\s*(?:Shell\s*Credits?)", re.I),
    re.compile(r"\d[\d,]*\s*(?:EXP\s*Tapes?|EXP\s*Materials?)", re.I),
]


# --------------------------------------------------------------------------
# FR-8: Context-aware redemption links
# --------------------------------------------------------------------------

def get_redeem_link(game: str, code: str) -> str | None:
    """Direct 1-click web redemption URL, or None if the game has no web redemption."""
    if game == "HSR":
        return f"https://hsr.hoyoverse.com/gift?code={code}"
    if game == "ZZZ":
        return f"https://zenless.hoyoverse.com/redemption?code={code}"
    # WuWa: no web redemption exists; frontend renders a "Copy Code" CTA instead (FR-8).
    return None


# --------------------------------------------------------------------------
# FR-4: Classification
# --------------------------------------------------------------------------

def classify_code(game: str, code: str, title: str, text: str) -> str:
    """Buckets a code into LIVESTREAM / VERSION / PROMO / PERMANENT."""
    if code in PERMANENT_CODES.get(game, []):
        return "PERMANENT"

    blob = f"{title} {text}".lower()

    if any(k in blob for k in ("livestream", "live stream", "special program", "dev letter broadcast")):
        return "LIVESTREAM"
    if any(k in blob for k in ("version", "patch", "update 1.", "hotfix")):
        return "VERSION"
    if any(k in blob for k in ("twitch", "discord", "sponsor", "partnership", "giveaway", "collab")):
        return "PROMO"

    # Default: most one-off codes posted outside a known livestream/version window are promo drops.
    return "PROMO"


def extract_rewards(title: str, text: str) -> str:
    """Best-effort reward summary. Falls back to a clear placeholder rather than guessing."""
    blob = f"{title} {text}"
    hits = []
    for pattern in REWARD_PATTERNS:
        for match in pattern.findall(blob):
            hits.append(match if isinstance(match, str) else match[0])
    if hits:
        # de-dupe while preserving order
        seen = set()
        ordered = [h for h in hits if not (h.lower() in seen or seen.add(h.lower()))]
        return ", ".join(ordered)
    return "Rewards unspecified - check source"


# --------------------------------------------------------------------------
# FR-1: Source fetchers
# --------------------------------------------------------------------------

def fetch_reddit_codes(game_id: str, subreddit: str) -> list[dict]:
    """Scrapes the latest posts from a subreddit looking for codes."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
    found = []

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        posts = response.json().get("data", {}).get("children", [])

        for post in posts:
            data = post["data"]
            title = data.get("title", "")
            text = data.get("selftext", "")

            if not any(k in title.lower() for k in ("code", "redeem", "livestream", "gem")):
                continue

            matches = set(CODE_REGEX.findall(title) + CODE_REGEX.findall(text))
            for code in matches:
                if code in FALSE_POSITIVES:
                    continue
                found.append({
                    "game": game_id,
                    "code": code,
                    "code_type": classify_code(game_id, code, title, text),
                    "rewards": extract_rewards(title, text),
                    "source_url": f"https://reddit.com{data.get('permalink')}",
                })
    except Exception as e:
        print(f"[!] Reddit fetch failed for {game_id}: {e}")

    return found


def fetch_wiki_codes(game_id: str, api_url: str, page_title: str) -> list[dict]:
    """Fetches HTML from the Fandom MediaWiki API and extracts codes from <code> tags."""
    found = []
    params = {"action": "parse", "page": page_title, "format": "json", "prop": "text"}

    try:
        response = requests.get(api_url, params=params, headers=HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()
        html_content = data.get("parse", {}).get("text", {}).get("*", "")
        soup = BeautifulSoup(html_content, "html.parser")

        for code_tag in soup.find_all("code"):
            code_text = code_tag.get_text(strip=True).upper()
            if not CODE_REGEX.fullmatch(code_text):
                continue

            # Grab surrounding row text (Fandom code tables usually put rewards in the next <td>)
            row_text = ""
            parent_row = code_tag.find_parent("tr")
            if parent_row:
                row_text = parent_row.get_text(" ", strip=True)

            found.append({
                "game": game_id,
                "code": code_text,
                "code_type": classify_code(game_id, code_text, "", row_text),
                "rewards": extract_rewards("", row_text),
                "source_url": f"{api_url.split('/api.php')[0]}/wiki/{page_title}",
            })
    except Exception as e:
        print(f"[!] Wiki fetch failed for {game_id}: {e}")

    return found


# --------------------------------------------------------------------------
# FR-2: Deduplication + Supabase upsert
# --------------------------------------------------------------------------

def dedupe(master_list: list[dict]) -> list[dict]:
    """In-memory dedup by (game, code) before we even hit the DB - cheap first pass."""
    unique = {}
    for item in master_list:
        key = (item["game"], item["code"])
        if key not in unique:
            unique[key] = {
                "game": item["game"],
                "code": item["code"],
                "code_type": item["code_type"],
                "rewards": item["rewards"],
                "status": "ACTIVE",
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": None,  # Set manually/admin-side, or by a future expiry-inference pass
                "source_url": item["source_url"],
                "direct_redeem_url": get_redeem_link(item["game"], item["code"]),
            }
    return list(unique.values())


def upsert_to_supabase(rows: list[dict]) -> None:
    """
    Upserts rows into `game_codes` via Supabase's PostgREST endpoint.
    Relies on the `uq_game_code UNIQUE (game, code)` constraint from the schema,
    so re-running the pipeline is idempotent (this IS the DB-level half of FR-2).
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        print("[!] SUPABASE_URL / SUPABASE_KEY not set - skipping DB write, printing instead.")
        print(json.dumps(rows, indent=2))
        return

    endpoint = f"{url}/rest/v1/game_codes?on_conflict=game,code"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # merge-duplicates: update status/rewards/etc. on conflict, don't skip silently
        "Prefer": "resolution=merge-duplicates,return=representation",
    }

    try:
        response = requests.post(endpoint, headers=headers, json=rows, timeout=15)
        response.raise_for_status()
        inserted = response.json()
        print(f"[+] Upserted {len(inserted)} rows into game_codes.")
    except Exception as e:
        print(f"[!] Supabase upsert failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(e.response.text)


# --------------------------------------------------------------------------
# Pipeline entrypoint
# --------------------------------------------------------------------------

def run_pipeline():
    print(f"--- Starting Scrape Pipeline at {datetime.now(timezone.utc).isoformat()} ---")
    master_list = []

    for game, config in GAMES.items():
        print(f"Fetching data for {game}...")
        
        # BYE REDDIT: Just comment this line out!
        # master_list.extend(fetch_reddit_codes(game, config["reddit"]))
        
        # KEEP WIKI: This is all you actually need
        master_list.extend(fetch_wiki_codes(game, config["wiki_url"], config["wiki_page"]))

    final_rows = dedupe(master_list)
    print(f"Pipeline complete. Discovered {len(final_rows)} unique candidate codes.")
    upsert_to_supabase(final_rows)


if __name__ == "__main__":
    run_pipeline()
