-- ============================================================================
-- Gacha Code Tracker - Phase 2: Database Setup
-- Run this in Supabase Dashboard > SQL Editor (or via `supabase db push`)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Core tables (verbatim from PDR Section 5, schema is not changed)
-- ---------------------------------------------------------------------------

CREATE TABLE game_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game VARCHAR(10) NOT NULL CHECK (game IN ('ZZZ', 'WUWA', 'HSR')),
    code VARCHAR(64) NOT NULL,
    code_type VARCHAR(20) NOT NULL CHECK (code_type IN ('LIVESTREAM', 'VERSION', 'PROMO', 'PERMANENT')),
    rewards TEXT NOT NULL,
    status VARCHAR(15) DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'EXPIRED', 'UNKNOWN')),
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NULL,
    source_url TEXT NULL,
    direct_redeem_url TEXT NULL,
    CONSTRAINT uq_game_code UNIQUE (game, code)
);

CREATE TABLE notification_subscribers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    notify_hsr BOOLEAN DEFAULT TRUE,
    notify_zzz BOOLEAN DEFAULT TRUE,
    notify_wuwa BOOLEAN DEFAULT TRUE,
    livestream_only BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE notification_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code_id UUID REFERENCES game_codes(id) ON DELETE CASCADE,
    subscriber_id UUID REFERENCES notification_subscribers(id) ON DELETE CASCADE,
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    status VARCHAR(20) DEFAULT 'SENT'
);

-- ---------------------------------------------------------------------------
-- 2. Indexes - the UNIQUE constraint on (game, code) already gives us a
--    b-tree index for the dedup check (FR-2). These add fast paths for the
--    dashboard's tab filtering (FR-6) and the notifier's "what's new" query.
-- ---------------------------------------------------------------------------

CREATE INDEX idx_game_codes_game_status ON game_codes (game, status);
CREATE INDEX idx_game_codes_discovered_at ON game_codes (discovered_at DESC);
CREATE INDEX idx_notification_logs_subscriber ON notification_logs (subscriber_id);

-- ---------------------------------------------------------------------------
-- 3. Seed data - permanent starter codes (PDR Section 2, "Returning Player").
--    These ship pre-seeded so a fresh dashboard isn't empty, and so the
--    scraper's classify_code() PERMANENT_CODES list matches what's in the DB.
-- ---------------------------------------------------------------------------

INSERT INTO game_codes (game, code, code_type, rewards, status, source_url, direct_redeem_url)
VALUES
    ('HSR', 'STARRAILGIFT', 'PERMANENT', '50 Stellar Jades, 10000 Credits', 'ACTIVE',
     'https://hsr.hoyoverse.com', 'https://hsr.hoyoverse.com/gift?code=STARRAILGIFT'),
    ('ZZZ', 'ZENLESSGIFT', 'PERMANENT', '50 Polychromes', 'ACTIVE',
     'https://zenless.hoyoverse.com', 'https://zenless.hoyoverse.com/redemption?code=ZENLESSGIFT'),
    ('WUWA', 'WUTHERINGGIFT', 'PERMANENT', '50 Astrites', 'ACTIVE',
     'https://wutheringwaves.kurogames.com', NULL)
ON CONFLICT (game, code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 4. Row Level Security
--    Supabase enables RLS by default on new tables with NO policies, which
--    means the public dashboard (using the anon key) would see zero rows
--    until a SELECT policy exists. The scraper and notifier use the
--    service_role key, which bypasses RLS entirely - so they need no policy.
-- ---------------------------------------------------------------------------

ALTER TABLE game_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_subscribers ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_logs ENABLE ROW LEVEL SECURITY;

-- Public dashboard: anyone can read active/expired codes (it's not sensitive data).
CREATE POLICY "Public can read game codes"
    ON game_codes FOR SELECT
    USING (true);

-- Subscribers table: no public SELECT policy - emails should not be readable
-- by the anon key. Inserts (sign-ups) are allowed from the public form.
CREATE POLICY "Anyone can subscribe"
    ON notification_subscribers FOR INSERT
    WITH CHECK (true);

-- notification_logs: no public policies at all - service_role only.

-- ---------------------------------------------------------------------------
-- 5. Convenience view for the notifier: codes that need to go out but
--    haven't yet been logged as sent to a given active subscriber.
--    (Used by the Phase 2 notification worker - not required by the dashboard.)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW pending_notifications AS
SELECT
    gc.id AS code_id,
    gc.game,
    gc.code,
    gc.code_type,
    gc.rewards,
    gc.expires_at,
    ns.id AS subscriber_id,
    ns.email
FROM game_codes gc
CROSS JOIN notification_subscribers ns
WHERE gc.status = 'ACTIVE'
  AND gc.code_type <> 'PERMANENT'                       -- FR-10: no spam for starter codes
  AND ns.is_active = true
  AND (
        (gc.game = 'HSR'  AND ns.notify_hsr  = true) OR
        (gc.game = 'ZZZ'  AND ns.notify_zzz  = true) OR
        (gc.game = 'WUWA' AND ns.notify_wuwa = true)
      )
  AND (ns.livestream_only = false OR gc.code_type = 'LIVESTREAM')
  AND NOT EXISTS (
        SELECT 1 FROM notification_logs nl
        WHERE nl.code_id = gc.id AND nl.subscriber_id = ns.id
      );
