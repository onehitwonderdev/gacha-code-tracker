import { useState, useMemo, useEffect } from "react";
import { Copy, Check, ExternalLink, Clock, Gift, Loader2 } from "lucide-react";

// ---------------------------------------------------------------------------
// Fill these in with YOUR project's values (Project Settings > API Keys).
// Use the PUBLISHABLE key (sb_publishable_...) here - never the secret key.
// The publishable key is safe to expose in frontend code; it only has the
// access your RLS policies grant it (see supabase_setup.sql - the
// "Public can read game codes" policy is what makes this work).
// ---------------------------------------------------------------------------
const SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co";
const SUPABASE_ANON_KEY = "YOUR_PUBLISHABLE_KEY";

// Legacy `anon` keys are JWTs and need Authorization: Bearer; new
// sb_publishable_... keys are opaque and only need the apikey header
// (sending them as Bearer gets rejected - see the sb_secret_ notes in
// dedup_check.py for the same gotcha on the server side).
function supabaseHeaders() {
  const headers = { apikey: SUPABASE_ANON_KEY };
  if (SUPABASE_ANON_KEY.startsWith("eyJ")) {
    headers["Authorization"] = `Bearer ${SUPABASE_ANON_KEY}`;
  }
  return headers;
}

// ---------------------------------------------------------------------------
// Fallback sample data - shown automatically if the constants above are
// still placeholders, or if the live fetch fails, so this component never
// renders empty.
// ---------------------------------------------------------------------------
const MOCK_CODES = [
  { id: 1, game: "HSR", code: "SPACE2026JADE", code_type: "LIVESTREAM", rewards: "300 Stellar Jades, 50000 Credits", status: "ACTIVE", expires_at: hoursFromNow(9), direct_redeem_url: "https://hsr.hoyoverse.com/gift?code=SPACE2026JADE" },
  { id: 2, game: "ZZZ", code: "PROXYUPDATE21", code_type: "VERSION", rewards: "200 Polychromes, 20000 Dennies", status: "ACTIVE", expires_at: daysFromNow(12), direct_redeem_url: "https://zenless.hoyoverse.com/redemption?code=PROXYUPDATE21" },
  { id: 3, game: "WUWA", code: "RESONATOR88", code_type: "PROMO", rewards: "150 Astrites, 10 Advanced Tuners", status: "ACTIVE", expires_at: daysFromNow(4), direct_redeem_url: null },
  { id: 4, game: "HSR", code: "STARRAILGIFT", code_type: "PERMANENT", rewards: "50 Stellar Jades, 10000 Credits", status: "ACTIVE", expires_at: null, direct_redeem_url: "https://hsr.hoyoverse.com/gift?code=STARRAILGIFT" },
  { id: 5, game: "ZZZ", code: "ZENLESSGIFT", code_type: "PERMANENT", rewards: "50 Polychromes", status: "ACTIVE", expires_at: null, direct_redeem_url: "https://zenless.hoyoverse.com/redemption?code=ZENLESSGIFT" },
  { id: 6, game: "WUWA", code: "WUTHERINGGIFT", code_type: "PERMANENT", rewards: "50 Astrites", status: "ACTIVE", expires_at: null, direct_redeem_url: null },
  { id: 7, game: "HSR", code: "HSRTWITCHDROP", code_type: "PROMO", rewards: "1 Stellar Jade x40 pull ticket bundle", status: "ACTIVE", expires_at: daysFromNow(2), direct_redeem_url: "https://hsr.hoyoverse.com/gift?code=HSRTWITCHDROP" },
  { id: 8, game: "WUWA", code: "TIDALWAVE500", code_type: "LIVESTREAM", rewards: "500 Astrites, 100000 Shell Credits", status: "ACTIVE", expires_at: hoursFromNow(14), direct_redeem_url: null },
];

function hoursFromNow(h) { return new Date(Date.now() + h * 3600 * 1000).toISOString(); }
function daysFromNow(d) { return new Date(Date.now() + d * 86400 * 1000).toISOString(); }

const GAME_META = {
  HSR:  { label: "Honkai: Star Rail", short: "HSR",  hue: "#8B6FE0", tint: "rgba(139,111,224,0.14)" },
  ZZZ:  { label: "Zenless Zone Zero", short: "ZZZ",  hue: "#F2C230", tint: "rgba(242,194,48,0.14)"  },
  WUWA: { label: "Wuthering Waves",   short: "WuWa", hue: "#3FBF9F", tint: "rgba(63,191,159,0.16)"  },
};

const TYPE_META = {
  LIVESTREAM: { label: "Livestream", color: "#FF5C7A" },
  VERSION:    { label: "Version",    color: "#6FA8FF" },
  PROMO:      { label: "Promo",      color: "#F2C230" },
  PERMANENT:  { label: "Permanent",  color: "#8A8496" },
};

function timeRemaining(expiresAt) {
  if (!expiresAt) return { label: "Permanent", urgent: false };
  const diffMs = new Date(expiresAt).getTime() - Date.now();
  if (diffMs <= 0) return { label: "Expired", urgent: false };
  const hrs = diffMs / 3600000;
  if (hrs < 24) return { label: `~${Math.round(hrs)}h left`, urgent: hrs <= 12 };
  return { label: `~${Math.round(hrs / 24)}d left`, urgent: false };
}

function CopyButton({ code }) {
  const [copied, setCopied] = useState(false);
  const onCopy = () => {
    navigator.clipboard?.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button
      onClick={onCopy}
      className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors"
      style={{
        borderColor: copied ? "#3FBF9F" : "rgba(255,255,255,0.14)",
        color: copied ? "#3FBF9F" : "#D8D4E6",
        background: copied ? "rgba(63,191,159,0.1)" : "rgba(255,255,255,0.03)",
      }}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function ActionButton({ item }) {
  if (item.direct_redeem_url) {
    return (
      <a
        href={item.direct_redeem_url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold text-[#15121C] transition-transform hover:scale-[1.03]"
        style={{ background: "#E4B44A" }}
      >
        Redeem <ExternalLink size={12} />
      </a>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <CopyButton code={item.code} />
      <span className="text-[10.5px] leading-tight text-[#8A8496]">
        In-game: Settings &gt; Other &gt; Redemption Code
      </span>
    </div>
  );
}

function TypeBadge({ type }) {
  const meta = TYPE_META[type];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ color: meta.color, background: `${meta.color}1F` }}
    >
      {type === "LIVESTREAM" && <span className="h-1.5 w-1.5 rounded-full" style={{ background: meta.color }} />}
      {meta.label}
    </span>
  );
}

function ExpiryBadge({ expiresAt }) {
  const { label, urgent } = timeRemaining(expiresAt);
  return (
    <span
      className="inline-flex items-center gap-1 text-xs font-medium"
      style={{ color: urgent ? "#FF5C7A" : "#B7B2C6" }}
    >
      <Clock size={12} />
      {label}
    </span>
  );
}

export default function GachaCodeTracker() {
  const [tab, setTab] = useState("ALL");
  const [codes, setCodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [usingMock, setUsingMock] = useState(false);

  useEffect(() => {
    const isPlaceholder = SUPABASE_URL.includes("YOUR_PROJECT_REF") || SUPABASE_ANON_KEY.includes("YOUR_PUBLISHABLE_KEY");

    if (isPlaceholder) {
      setCodes(MOCK_CODES);
      setUsingMock(true);
      setLoading(false);
      return;
    }

    const url = `${SUPABASE_URL}/rest/v1/game_codes?select=*&status=eq.ACTIVE&order=discovered_at.desc`;

    fetch(url, { headers: supabaseHeaders() })
      .then((res) => {
        if (!res.ok) throw new Error(`Supabase returned ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setCodes(data);
        setUsingMock(false);
      })
      .catch((err) => {
        console.error("Falling back to sample data:", err);
        setCodes(MOCK_CODES);
        setUsingMock(true);
      })
      .finally(() => setLoading(false));
  }, []);

  const tabs = [
    { key: "ALL", label: "All games" },
    { key: "HSR", label: "Star Rail" },
    { key: "ZZZ", label: "Zenless" },
    { key: "WUWA", label: "Wuthering" },
  ];

  const rows = useMemo(() => {
    const filtered = tab === "ALL" ? codes : codes.filter((c) => c.game === tab);
    // Livestream codes float to the top - they're the ones about to expire.
    return [...filtered].sort((a, b) => {
      const order = { LIVESTREAM: 0, VERSION: 1, PROMO: 2, PERMANENT: 3 };
      return (order[a.code_type] ?? 9) - (order[b.code_type] ?? 9);
    });
  }, [tab, codes]);

  return (
    <div
      className="min-h-screen w-full px-4 py-8 sm:px-8"
      style={{ background: "#15121C", fontFamily: "ui-sans-serif, system-ui, sans-serif" }}
    >
      <div className="mx-auto max-w-4xl">
        {/* Header */}
        <div className="mb-7 flex items-start gap-3">
          <div
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl"
            style={{ background: "linear-gradient(135deg, #E4B44A, #FF5C7A)" }}
          >
            <Gift size={20} color="#15121C" strokeWidth={2.5} />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-[#F3F1F8]">Gacha code tracker</h1>
            <p className="mt-0.5 text-sm text-[#8A8496]">
              Live codes for Star Rail, Zenless Zone Zero, and Wuthering Waves, pulled from Reddit and the wikis every 15 minutes.
            </p>
          </div>
        </div>

        {/* Sample-data notice */}
        {usingMock && !loading && (
          <div
            className="mb-5 rounded-lg border px-3.5 py-2.5 text-xs"
            style={{ borderColor: "rgba(228,180,74,0.35)", background: "rgba(228,180,74,0.08)", color: "#E4B44A" }}
          >
            Showing sample data. Add your Supabase project URL and publishable key at the top of this file to connect live codes.
          </div>
        )}

        {/* Tabs */}
        <div className="mb-5 flex gap-1.5 overflow-x-auto rounded-lg bg-white/[0.03] p-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="whitespace-nowrap rounded-md px-3.5 py-1.5 text-sm font-medium transition-colors"
              style={{
                background: tab === t.key ? "rgba(255,255,255,0.08)" : "transparent",
                color: tab === t.key ? "#F3F1F8" : "#8A8496",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {loading && (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-[#8A8496]">
            <Loader2 size={16} className="animate-spin" />
            Loading codes...
          </div>
        )}

        {/* Desktop table */}
        {!loading && (
        <div className="hidden overflow-hidden rounded-xl border border-white/[0.07] md:block">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-white/[0.07] text-left text-xs text-[#8A8496]">
                <th className="px-4 py-3 font-medium">Game</th>
                <th className="px-4 py-3 font-medium">Code</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium">Rewards</th>
                <th className="px-4 py-3 font-medium">Expiry</th>
                <th className="px-4 py-3 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item, i) => {
                const meta = GAME_META[item.game];
                return (
                  <tr
                    key={item.id}
                    className="border-b border-white/[0.05] last:border-0"
                    style={{ background: i % 2 === 1 ? "rgba(255,255,255,0.015)" : "transparent" }}
                  >
                    <td className="px-4 py-3">
                      <span
                        className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold"
                        style={{ color: meta.hue, background: meta.tint }}
                      >
                        {meta.short}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-[13px] text-[#F3F1F8]">{item.code}</td>
                    <td className="px-4 py-3"><TypeBadge type={item.code_type} /></td>
                    <td className="px-4 py-3 max-w-[220px] text-[#B7B2C6]">{item.rewards}</td>
                    <td className="px-4 py-3"><ExpiryBadge expiresAt={item.expires_at} /></td>
                    <td className="px-4 py-3"><ActionButton item={item} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}

        {/* Mobile stacked cards (NFR-4) */}
        {!loading && (
        <div className="flex flex-col gap-3 md:hidden">
          {rows.map((item) => {
            const meta = GAME_META[item.game];
            return (
              <div key={item.id} className="rounded-xl border border-white/[0.07] p-4" style={{ background: "rgba(255,255,255,0.02)" }}>
                <div className="mb-2.5 flex items-center justify-between">
                  <span
                    className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold"
                    style={{ color: meta.hue, background: meta.tint }}
                  >
                    {meta.short}
                  </span>
                  <TypeBadge type={item.code_type} />
                </div>
                <div className="mb-1 font-mono text-[15px] font-medium text-[#F3F1F8]">{item.code}</div>
                <div className="mb-3 text-sm text-[#B7B2C6]">{item.rewards}</div>
                <div className="flex items-center justify-between">
                  <ExpiryBadge expiresAt={item.expires_at} />
                  <ActionButton item={item} />
                </div>
              </div>
            );
          })}
        </div>
        )}

        {!loading && rows.length === 0 && (
          <div className="rounded-xl border border-white/[0.07] py-12 text-center text-sm text-[#8A8496]">
            No active codes for this game right now — check back after the next scrape.
          </div>
        )}
      </div>
    </div>
  );
}