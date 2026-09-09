"""
Gacha Code Tracker - Phase 2: Deduplication Check

FR-2 says candidate codes must be checked against the DB's unique
constraint *before* triggering downstream events (i.e. before an email
blast goes out). scraper.py's upsert already relies on the DB-level
`uq_game_code` constraint for storage-level dedup - this module is the
extra check the notifier runs so it never re-alerts on a code it has
already processed.

Requires: pip install requests
Env vars: SUPABASE_URL, SUPABASE_KEY
    SUPABASE_KEY should be the sb_secret_... key from Project Settings > API Keys
    (or the legacy service_role JWT, only if your project still issues one).
    Never hardcode this value - load it from the environment only.
"""

import os
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


def _headers():
    # New-style keys (sb_secret_..., sb_publishable_...) are opaque, not JWTs.
    # Supabase's gateway rejects them if sent in Authorization: Bearer - it tries
    # to parse that header as a JWT and fails. Only the apikey header is needed.
    # Legacy service_role JWTs (eyJ...) still need Authorization for PostgREST
    # to read the role claim.
    headers = {"apikey": SUPABASE_KEY}
    if SUPABASE_KEY and SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    return headers


def _raise_with_hint(response: requests.Response):
    if response.status_code == 401:
        raise RuntimeError(
            "401 Unauthorized from Supabase. Response body: "
            f"{response.text}\n"
            "If SUPABASE_KEY is a legacy service_role JWT, check Project Settings > API Keys "
            "in the dashboard - projects created after Nov 1, 2025 no longer issue/accept "
            "those by default. Use the sb_secret_... key shown there instead."
        )
    response.raise_for_status()


def get_existing_codes(game: str) -> set[str]:
    """Returns the set of codes already stored for a game."""
    url = f"{SUPABASE_URL}/rest/v1/game_codes"
    params = {"game": f"eq.{game}", "select": "code"}
    response = requests.get(url, headers=_headers(), params=params, timeout=10)
    _raise_with_hint(response)
    return {row["code"] for row in response.json()}


def filter_new_candidates(candidates: list[dict]) -> list[dict]:
    """
    Given a list of candidate rows (as produced by scraper.dedupe()),
    returns only the ones NOT already present in game_codes.
    Groups by game first so we only hit the DB once per game, not once per code.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY must be set to run the dedup check.")

    by_game: dict[str, set[str]] = {}
    new_rows = []

    for row in candidates:
        game = row["game"]
        if game not in by_game:
            by_game[game] = get_existing_codes(game)

        if row["code"] not in by_game[game]:
            new_rows.append(row)

    return new_rows


def check_pending_notifications() -> list[dict]:
    """
    Reads the `pending_notifications` view (defined in supabase_setup.sql) -
    the actual "does this need to go out" check the notifier should use,
    since it already excludes PERMANENT codes and already-sent combinations
    at the DB level.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY must be set to run the dedup check.")

    url = f"{SUPABASE_URL}/rest/v1/pending_notifications"
    response = requests.get(url, headers=_headers(), timeout=10)
    _raise_with_hint(response)
    return response.json()


if __name__ == "__main__":
    pending = check_pending_notifications()
    print(f"{len(pending)} notification(s) pending dispatch.")
    for item in pending[:10]:
        print(f"  - {item['game']} {item['code']} ({item['code_type']}) -> {item['email']}")