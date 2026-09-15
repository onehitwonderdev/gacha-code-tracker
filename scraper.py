"""
Gacha Code Tracker - Phase 1: Scraping & Ingestion Engine
Implements FR-1 (multi-source polling), FR-2 (dedup), FR-4 (classification)
against the `game_codes` schema defined in the PDR (Section 5).

Sources polled per game (FR-1): Reddit (fastest, noisiest - best-effort
active/expired keyword detection only), the Fandom wiki (curated, has a
real active/expired table split), and Game8 (curated, also has a real
active/expired table split - a second reliable, independently-maintained
source alongside the wiki, per game in GAMES[game]["game8_url"]).

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
        # Game8's evergreen monthly code roundup - same article ID persists
        # across version updates (they edit it in place rather than posting
        # a new URL each patch), so this is safe to poll indefinitely.
        "game8_url": "https://game8.co/games/Honkai-Star-Rail/archives/410296",
    },
    "ZZZ": {
        "reddit": "ZenlessZoneZero",
        "wiki_url": "https://zenless-zone-zero.fandom.com/api.php",
        "wiki_page": "Redemption_Code",
        "game8_url": "https://game8.co/games/Zenless-Zone-Zero/archives/435683",
    },
    "WUWA": {
        "reddit": "WutheringWaves",
        "wiki_url": "https://wutheringwaves.fandom.com/api.php",
        "wiki_page": "Redemption_Code",
        "game8_url": "https://game8.co/games/Wuthering-Waves/archives/453149",
    },
}

# Reddit requires a descriptive User-Agent or it will 429 you.
HEADERS = {
    "User-Agent": "python:gacha_tracker_bot:v1.0 (by /u/your_reddit_username)"
}

# Uppercase alphanumeric, 6-16 chars - typical gacha code shape.
CODE_REGEX = re.compile(r"\b[A-Z0-9]{6,16}\b")

# Words that match the regex shape but are never actual codes.
# The COPIED/REDEEM/HERE entries are defense-in-depth for Game8's button
# chrome ("Copied / Redeem <CODE> Here") in case a future markup change ever
# puts that text through .upper() before matching.
FALSE_POSITIVES = {
    "LIVESTREAM", "UPDATE", "REDDIT", "YOUTUBE", "TWITTER", "OFFICIAL",
    "COPIED", "REDEEM", "HERE",
}

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


# Reddit posts that explicitly call a code out as dead - either via flair
# (some subs tag threads "Expired"/"Outdated" once confirmed) or in the
# title/body itself (a very common phrasing on megathreads and follow-up
# comments-turned-posts).
REDDIT_EXPIRED_FLAIRS = ("expired", "outdated", "inactive")
REDDIT_EXPIRED_PHRASES = (
    "expired", "no longer works", "doesn't work anymore", "does not work anymore",
    "already expired", "code is dead", "codes are dead",
)


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
            flair = (data.get("link_flair_text") or "").lower()

            if not any(k in title.lower() for k in ("code", "redeem", "livestream", "gem")):
                continue

            # FR-2 fix: this used to hardcode status="ACTIVE" for every code
            # found on Reddit, regardless of what the post actually said.
            # That's wrong for reposts/aggregator threads and for posts made
            # specifically to warn people a code died - both are common here.
            # Reddit doesn't give us a structured active/expired signal like
            # the wiki's table sections do, so this is a best-effort keyword
            # check on the flair + title + body; dedupe() still lets a
            # wiki/Game8 EXPIRED verdict override an ACTIVE guess from here,
            # but not vice versa, so false negatives here are the safer
            # failure mode than false positives.
            blob = f"{title} {text}".lower()
            is_expired = (
                any(w in flair for w in REDDIT_EXPIRED_FLAIRS)
                or any(p in blob for p in REDDIT_EXPIRED_PHRASES)
            )
            status = "EXPIRED" if is_expired else "ACTIVE"

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
                    "status": status,
                })
    except Exception as e:
        print(f"[!] Reddit fetch failed for {game_id}: {e}")

    return found


def _section_status_for(tag) -> str:
    """
    Walks backwards from a <code> tag to the nearest preceding heading (h2/h3/h4)
    to figure out whether it's in an 'active' or 'expired' section of the wiki page.
    Fandom code pages almost always split codes into two tables like this - if we
    don't check which one a code came from, an expired code gets scraped as ACTIVE.
    """
    EXPIRED_WORDS = ("expired", "inactive", "old code", "no longer")
    for heading in tag.find_all_previous(["h2", "h3", "h4"]):
        heading_text = heading.get_text(" ", strip=True).lower()
        if any(w in heading_text for w in EXPIRED_WORDS):
            return "EXPIRED"
        # First heading found going backwards that ISN'T about expired codes -
        # assume it marks the start of the active section, stop looking further up.
        if heading_text:
            return "ACTIVE"
    return "ACTIVE"  # no heading found at all - default to active rather than guess wrong


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

            status = _section_status_for(code_tag)

            found.append({
                "game": game_id,
                "code": code_text,
                "code_type": classify_code(game_id, code_text, "", row_text),
                "rewards": extract_rewards("", row_text),
                "source_url": f"{api_url.split('/api.php')[0]}/wiki/{page_title}",
                "status": status,
            })
    except Exception as e:
        print(f"[!] Wiki fetch failed for {game_id}: {e}")

    return found


def fetch_game8_codes(game_id: str, url: str) -> list[dict]:
    """
    Fetches a Game8 redeem-code guide page directly (Game8 has no public API,
    unlike the Fandom wikis, so this is a plain HTML GET + parse).

    Game8's code guides use the same layout convention Fandom does: an
    "Active/current codes" table, followed further down the page by an
    "All Expired ... Codes" table - so _section_status_for's heading-walk
    (originally written for the Fandom wiki fetcher) applies unchanged here
    too. The one real difference is that Game8 doesn't wrap codes in <code>
    tags - they're just plain text in table cells - so we scan <td> cells
    instead.

    This is added as a second, independently-maintained reliable source
    alongside the wiki: Game8 pages are curated/edited by staff, get taken
    down or corrected quickly when a code stops working, and give us a
    genuine expired-codes table rather than the Reddit fetcher's best-effort
    keyword guess.
    """
    found = []

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for cell in soup.find_all("td"):
            # NOTE: deliberately NOT upper()-ing the whole cell before
            # matching. Game8's code cells aren't bare code text - they're
            # "Copied / Redeem <CODE> Here" button chrome around the code.
            # Upper-casing first would turn "Copied"/"Redeem"/"Here" into
            # false-positive code-shaped tokens. CODE_REGEX is already
            # case-sensitive (uppercase-only), so scanning the raw cell text
            # naturally picks out just the genuinely-uppercase code itself.
            cell_text = cell.get_text(" ", strip=True)
            for code_text in CODE_REGEX.findall(cell_text):
                if code_text in FALSE_POSITIVES:
                    continue

                row_text = ""
                parent_row = cell.find_parent("tr")
                if parent_row:
                    row_text = parent_row.get_text(" ", strip=True)

                status = _section_status_for(cell)

                found.append({
                    "game": game_id,
                    "code": code_text,
                    "code_type": classify_code(game_id, code_text, "", row_text),
                    "rewards": extract_rewards("", row_text),
                    "source_url": url,
                    "status": status,
                })
    except Exception as e:
        print(f"[!] Game8 fetch failed for {game_id}: {e}")

    return found


# --------------------------------------------------------------------------
# FR-2: Deduplication + Supabase upsert
# --------------------------------------------------------------------------

def dedupe(master_list: list[dict]) -> list[dict]:
    """In-memory dedup by (game, code) before we even hit the DB - cheap first pass."""
    unique = {}
    now = datetime.now(timezone.utc)

    for item in master_list:
        key = (item["game"], item["code"])
        if key in unique:
            # If we've already seen this code and EITHER source says EXPIRED,
            # prefer EXPIRED - it's a stronger signal than an ACTIVE guess.
            if item.get("status") == "EXPIRED":
                unique[key]["status"] = "EXPIRED"
            continue

        code_type = item["code_type"]
        status = item.get("status", "ACTIVE")

        # PDR Section 1: livestream codes expire in ~12-24h. We don't get an
        # exact expiry from any source, so seed a conservative 24h estimate
        # rather than leaving expires_at null - null reads as "Permanent" on
        # the dashboard, which is actively misleading for a code this short-lived.
        # Treat this as a starting estimate an admin can correct (FR-5).
        expires_at = None
        if code_type == "LIVESTREAM" and status == "ACTIVE":
            expires_at = (now.timestamp() + 24 * 3600)
            expires_at = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()

        unique[key] = {
            "game": item["game"],
            "code": item["code"],
            "code_type": code_type,
            "rewards": item["rewards"],
            "status": status,
            "discovered_at": now.isoformat(),
            "expires_at": expires_at,
            "source_url": item["source_url"],
            "direct_redeem_url": get_redeem_link(item["game"], item["code"]),
        }
    return list(unique.values())


def _supabase_headers(key: str) -> dict:
    """
    New-style keys (sb_secret_..., sb_publishable_...) are opaque, not JWTs -
    Supabase's gateway rejects them if sent as Authorization: Bearer (it tries
    to parse that header as a JWT and fails). Only apikey is needed for those.
    Legacy service_role JWTs (eyJ...) still need Authorization for PostgREST
    to read the role claim.
    """
    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


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
        **_supabase_headers(key),
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


def expire_stale_codes() -> None:
    """
    Flips any code from ACTIVE to EXPIRED once its expires_at has passed.
    This is the missing piece that actually verifies active/expired status
    over time - without it, a LIVESTREAM code we seeded a 24h estimate for
    (or a VERSION code with a manually-set expires_at) just sits ACTIVE
    forever even after the clock runs out, since nothing else revisits it.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    endpoint = f"{url}/rest/v1/game_codes?status=eq.ACTIVE&expires_at=lt.{now_iso}"
    headers = {
        **_supabase_headers(key),
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    try:
        response = requests.patch(endpoint, headers=headers, json={"status": "EXPIRED"}, timeout=15)
        response.raise_for_status()
        updated = response.json()
        if updated:
            print(f"[+] Marked {len(updated)} code(s) EXPIRED (past their expires_at).")
    except Exception as e:
        print(f"[!] Expiry sweep failed: {e}")


# --------------------------------------------------------------------------
# Pipeline entrypoint
# --------------------------------------------------------------------------

def run_pipeline():
    print(f"--- Starting Scrape Pipeline at {datetime.now(timezone.utc).isoformat()} ---")
    master_list = []

    for game, config in GAMES.items():
        print(f"Fetching data for {game}...")
        master_list.extend(fetch_reddit_codes(game, config["reddit"]))
        master_list.extend(fetch_wiki_codes(game, config["wiki_url"], config["wiki_page"]))
        master_list.extend(fetch_game8_codes(game, config["game8_url"]))

    final_rows = dedupe(master_list)
    print(f"Pipeline complete. Discovered {len(final_rows)} unique candidate codes.")

    upsert_to_supabase(final_rows)
    expire_stale_codes()


if __name__ == "__main__":
    run_pipeline()