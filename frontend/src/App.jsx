import { useState, useMemo, useEffect } from "react";
import {
  Copy, Check, ExternalLink, Clock, Gift, Loader2, AlertCircle,
  Search, ArrowUp, ArrowDown, ArrowUpDown, ChevronLeft, ChevronRight,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Fill these in with YOUR project's values (Project Settings > API Keys).
// Use the PUBLISHABLE key (sb_publishable_...) here - never the secret key.
// The publishable key is safe to expose in frontend code; it only has the
// access your RLS policies grant it (see supabase_setup.sql - the
// "Public can read game codes" policy is what makes this work).
// ---------------------------------------------------------------------------
const SUPABASE_URL = "https://zgzbchsuzjxronkzrlwk.supabase.co";
const SUPABASE_ANON_KEY = "sb_publishable_c5eCZcct5rbOkdgoSPrU8Q_r6u_54Lz";

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


const GAME_META = {
  HSR:  { label: "Honkai: Star Rail", short: "HSR",  hue: "#8B6FE0", tint: "rgba(139,111,224,0.14)" },
  ZZZ:  { label: "Zenless Zone Zero", short: "ZZZ",  hue: "#F2C230", tint: "rgba(242,194,48,0.14)"  },
  WUWA: { label: "Wuthering Waves",   short: "WuWa", hue: "#3FBF9F", tint: "rgba(63,191,159,0.16)"  },
};

const TYPE_META = {
  LIVESTREAM: { label: "Livestream", color: "#FF5C7A", order: 0 },
  VERSION:    { label: "Version",    color: "#6FA8FF", order: 1 },
  PROMO:      { label: "Promo",      color: "#F2C230", order: 2 },
  PERMANENT:  { label: "Permanent",  color: "#8A8496", order: 3 },
};

// Distinguishes an actual "never expires" starter code from a time-limited
// code we just don't have a known end date for yet - conflating the two
// is what made every un-dated WuWa promo code read as "Permanent" before.
function timeRemaining(expiresAt, codeType) {
  if (codeType === "PERMANENT") return { label: "Permanent", urgent: false, sortValue: Infinity };
  if (!expiresAt) return { label: "Unknown", urgent: false, sortValue: Infinity - 1 };

  const diffMs = new Date(expiresAt).getTime() - Date.now();
  if (diffMs <= 0) return { label: "Expired", urgent: false, sortValue: -Infinity };

  const hrs = diffMs / 3600000;
  if (hrs < 24) return { label: `~${Math.round(hrs)}h left`, urgent: hrs <= 12, sortValue: diffMs };
  return { label: `~${Math.round(hrs / 24)}d left`, urgent: false, sortValue: diffMs };
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
  const meta = TYPE_META[type] ?? { label: type, color: "#8A8496" };
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

function ExpiryBadge({ expiresAt, codeType }) {
  const { label, urgent } = timeRemaining(expiresAt, codeType);
  return (
    <span
      className="inline-flex items-center gap-1 text-xs font-medium"
      style={{ color: urgent ? "#FF5C7A" : label === "Unknown" ? "#6B6678" : "#B7B2C6" }}
    >
      <Clock size={12} />
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Sortable header cell
// ---------------------------------------------------------------------------
function SortHeader({ label, sortKey, activeSort, onSort }) {
  const isActive = activeSort.key === sortKey;
  const Icon = isActive ? (activeSort.dir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <th className="px-4 py-3 font-medium">
      <button
        onClick={() => onSort(sortKey)}
        className="inline-flex items-center gap-1 transition-colors hover:text-[#F3F1F8]"
        style={{ color: isActive ? "#F3F1F8" : "#8A8496" }}
      >
        {label}
        <Icon size={12} strokeWidth={2.25} className={isActive ? "" : "opacity-40"} />
      </button>
    </th>
  );
}

const PAGE_SIZE_OPTIONS = [10, 25, 50];

export default function GachaCodeTracker() {
  const [tab, setTab] = useState("ALL");
  const [codes, setCodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState({ key: "urgency", dir: "asc" });
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);

  useEffect(() => {
    const isPlaceholder = SUPABASE_URL.includes("YOUR_PROJECT_REF") || SUPABASE_ANON_KEY.includes("YOUR_PUBLISHABLE_KEY");

    if (isPlaceholder) {
      setError("Supabase isn't configured yet. Add your project URL and publishable key at the top of this file.");
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
        setError(null);
      })
      .catch((err) => {
        console.error("Failed to load codes:", err);
        setError(`Couldn't load codes (${err.message}). Check your Supabase URL/key, your network connection, and that the game_codes table is reachable, then refresh.`);
      })
      .finally(() => setLoading(false));
  }, []);

  const tabs = [
    { key: "ALL", label: "All games" },
    { key: "HSR", label: "Star Rail" },
    { key: "ZZZ", label: "Zenless" },
    { key: "WUWA", label: "Wuthering" },
  ];

  // Reset to page 1 whenever the active filters change, so you never land
  // on a now-empty page after narrowing a search or switching games.
  useEffect(() => { setPage(1); }, [tab, search, pageSize]);

  const handleSort = (key) => {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  };

  const filteredRows = useMemo(() => {
    let list = tab === "ALL" ? codes : codes.filter((c) => c.game === tab);

    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter((c) =>
        c.code.toLowerCase().includes(q) ||
        (c.rewards ?? "").toLowerCase().includes(q) ||
        (GAME_META[c.game]?.label ?? c.game).toLowerCase().includes(q)
      );
    }
    return list;
  }, [tab, search, codes]);

  const sortedRows = useMemo(() => {
    const list = [...filteredRows];
    const dirMult = sort.dir === "asc" ? 1 : -1;

    list.sort((a, b) => {
      switch (sort.key) {
        case "game":
          return dirMult * a.game.localeCompare(b.game);
        case "code":
          return dirMult * a.code.localeCompare(b.code);
        case "type":
          return dirMult * ((TYPE_META[a.code_type]?.order ?? 9) - (TYPE_META[b.code_type]?.order ?? 9));
        case "expiry": {
          const av = timeRemaining(a.expires_at, a.code_type).sortValue;
          const bv = timeRemaining(b.expires_at, b.code_type).sortValue;
          return dirMult * (av - bv);
        }
        case "urgency":
        default:
          // Default view: soonest-to-expire first, regardless of asc/desc toggle state.
          return (TYPE_META[a.code_type]?.order ?? 9) - (TYPE_META[b.code_type]?.order ?? 9);
      }
    });
    return list;
  }, [filteredRows, sort]);

  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const clampedPage = Math.min(page, totalPages);
  const pageRows = sortedRows.slice((clampedPage - 1) * pageSize, clampedPage * pageSize);

  return (
    <div
      className="min-h-screen w-full px-4 py-8 sm:px-8"
      style={{ background: "#15121C", fontFamily: "ui-sans-serif, system-ui, sans-serif" }}
    >
      <div className="mx-auto max-w-5xl">
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

        {/* Note - shown when there's no live data to display, with the real reason why */}
        {error && !loading && (
          <div
            className="mb-5 flex items-start gap-2 rounded-lg border px-3.5 py-2.5 text-xs"
            style={{ borderColor: "rgba(228,180,74,0.35)", background: "rgba(228,180,74,0.08)", color: "#E4B44A" }}
          >
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Tabs */}
        <div className="mb-4 flex gap-1.5 overflow-x-auto rounded-lg bg-white/[0.03] p-1">
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

        {/* Search + page size controls */}
        <div className="mb-4 flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative flex-1 sm:max-w-xs">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#6B6678]" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search code or reward..."
              className="w-full rounded-lg border border-white/[0.08] bg-white/[0.03] py-1.5 pl-8 pr-3 text-sm text-[#F3F1F8] placeholder:text-[#6B6678] outline-none focus:border-white/20"
            />
          </div>
          <div className="flex items-center gap-2 text-xs text-[#8A8496]">
            Show
            <select
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
              className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[#F3F1F8] outline-none"
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n} style={{ background: "#1E1A29" }}>{n}</option>
              ))}
            </select>
            per page
          </div>
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
                <SortHeader label="Game" sortKey="game" activeSort={sort} onSort={handleSort} />
                <SortHeader label="Code" sortKey="code" activeSort={sort} onSort={handleSort} />
                <SortHeader label="Type" sortKey="type" activeSort={sort} onSort={handleSort} />
                <th className="px-4 py-3 font-medium">Rewards</th>
                <SortHeader label="Expiry" sortKey="expiry" activeSort={sort} onSort={handleSort} />
                <th className="px-4 py-3 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((item, i) => {
                const meta = GAME_META[item.game] ?? { short: item.game, hue: "#8A8496", tint: "rgba(138,132,150,0.14)" };
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
                    <td className="px-4 py-3"><ExpiryBadge expiresAt={item.expires_at} codeType={item.code_type} /></td>
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
          {pageRows.map((item) => {
            const meta = GAME_META[item.game] ?? { short: item.game, hue: "#8A8496", tint: "rgba(138,132,150,0.14)" };
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
                  <ExpiryBadge expiresAt={item.expires_at} codeType={item.code_type} />
                  <ActionButton item={item} />
                </div>
              </div>
            );
          })}
        </div>
        )}

        {!loading && sortedRows.length === 0 && (
          <div className="rounded-xl border border-white/[0.07] py-12 text-center text-sm text-[#8A8496]">
            {error
              ? "No codes to show until this is connected — see the note above."
              : search
              ? "No codes match your search."
              : "No active codes for this game right now — check back after the next scrape."}
          </div>
        )}

        {/* Pagination */}
        {!loading && sortedRows.length > 0 && (
          <div className="mt-4 flex items-center justify-between text-xs text-[#8A8496]">
            <span>
              Showing {(clampedPage - 1) * pageSize + 1}-{Math.min(clampedPage * pageSize, sortedRows.length)} of {sortedRows.length}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={clampedPage === 1}
                className="flex items-center gap-1 rounded-md border border-white/[0.08] px-2.5 py-1.5 transition-colors disabled:opacity-30 enabled:hover:bg-white/[0.05] enabled:text-[#F3F1F8]"
              >
                <ChevronLeft size={13} /> Prev
              </button>
              <span className="px-1">Page {clampedPage} of {totalPages}</span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={clampedPage === totalPages}
                className="flex items-center gap-1 rounded-md border border-white/[0.08] px-2.5 py-1.5 transition-colors disabled:opacity-30 enabled:hover:bg-white/[0.05] enabled:text-[#F3F1F8]"
              >
                Next <ChevronRight size={13} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}