"""Investment Research Bot - watchlist monitor dashboard.

Free data stack:
  - SEC EDGAR  -> 10-K / 10-Q / 8-K / insider / proxy filings (authoritative)
  - Yahoo Finance (yfinance) -> news headlines + quote/price

Usage:
    streamlit run app.py
"""
import streamlit as st
from datetime import datetime, timezone

from fetch import (
    load_cik_map,
    recent_filings,
    news_for,
    quote_for,
    load_watchlist,
    save_watchlist,
    DEFAULT_WATCHLIST,
    FILING_LABELS,
)


st.set_page_config(page_title="Investment Research Bot", page_icon="📈", layout="wide")
st.title("📈 Investment Research Bot — Watchlist Monitor")
st.caption(
    "Free data: **SEC EDGAR** (filings + company names) and **Yahoo Finance** "
    "(news + quotes). No API key required. Data refreshes on demand — hit "
    "**Refresh** in the sidebar."
)


# ---------------------------------------------------------------- helpers
def _fmt_mcap(v):
    if v is None:
        return "—"
    # yfinance may return an int (raw dollars) or occasionally a string.
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.0f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def _filings_table(filings):
    if not filings:
        return "_No filings matching the selected types._"
    header = "| Date | Form | Type | Document | SEC Link |\n"
    header += "|------|------|------|----------|----------|\n"
    rows = []
    for f in filings:
        rows.append(
            f"| {f['date']} | {f['form']} | {f['label']} | "
            f"{f['primary_doc']} | [View on SEC →]({f['index_url']}) |"
        )
    return header + "\n".join(rows)


def _news_list(news):
    if not news:
        return "_No recent news._"
    if news[0].get("error"):
        return f"_⚠ {news[0]['error']}_"
    lines = []
    for n in news[:10]:
        title = n.get("title", "(no title)")
        pub = n.get("publisher") or n.get("source", "")
        dt = n.get("date", "")
        url = n.get("url", "")
        meta = f" — _{pub}_" + (f" · {dt}" if dt else "")
        if url:
            lines.append(f"- [{title}]({url}){meta}")
        else:
            lines.append(f"- **{title}**{meta}")
    return "\n".join(lines)


# ---------------------------------------------------------------- sidebar
st.sidebar.header("Watchlist")

# initialise / refresh session state from disk
def _sync():
    st.session_state.watchlist = load_watchlist()
    st.session_state.cache_map = load_cik_map()


if "watchlist" not in st.session_state:
    _sync()

wl = st.session_state.watchlist
cik_map = st.session_state.get("cache_map", load_cik_map())


# -- add ticker
new_ticker = st.sidebar.text_input(
    "Add ticker", placeholder="e.g. NVDA", help="Ticker must exist in SEC EDGAR's company list"
).strip().upper()

if st.sidebar.button("Add"):
    if not new_ticker:
        st.sidebar.warning("Enter a ticker symbol.")
    elif new_ticker in wl:
        st.sidebar.warning(f"{new_ticker} is already in the watchlist.")
    else:
        c, name = (cik_map.get(new_ticker.upper(), {}).get("cik"), None)
        entry = cik_map.get(new_ticker.upper())
        if entry:
            wl.append(new_ticker)
            save_watchlist(wl)
            st.session_state.watchlist = wl
            st.sidebar.success(f"Added {new_ticker} — {entry['name']}")
        else:
            st.sidebar.error(
                f"❌ '{new_ticker}' not found in SEC EDGAR. Check the symbol "
                "(e.g. BRK-B for Berkshire Hathaway Class B)."
            )

st.sidebar.divider()

# -- remove
to_remove = st.sidebar.multiselect(
    "Remove tickers", options=sorted(wl), default=[]
)
if st.sidebar.button("Remove selected"):
    if not to_remove:
        st.sidebar.warning("Select at least one ticker.")
    else:
        for t in to_remove:
            wl.remove(t)
        save_watchlist(wl)
        st.session_state.watchlist = wl
        st.sidebar.success(f"Removed {', '.join(sorted(to_remove))}.")

st.sidebar.divider()

# -- reset
if st.sidebar.button("Reset to defaults"):
    st.session_state.watchlist = list(DEFAULT_WATCHLIST)
    save_watchlist(DEFAULT_WATCHLIST)
    st.sidebar.success("Watchlist reset to defaults.")

st.sidebar.divider()
st.sidebar.caption(f"{len(wl)} ticker{'s' if len(wl) != 1 else ''} in watchlist · "
                   "Sources: SEC EDGAR + Yahoo Finance")


# -- filing type filter
st.sidebar.header("Filing types to show")
all_types = list(FILING_LABELS.keys())
selected_types = st.sidebar.multiselect(
    "Show these SEC form types",
    options=all_types,
    default=["8-K", "10-K", "10-Q", "4"],
    format_func=lambda x: f"{x} — {FILING_LABELS.get(x, x)}",
    help="8-K = material events · 10-K = annual · 10-Q = quarterly · 4 = insider trades",
)
if not selected_types:
    selected_types = all_types

# -- refresh button
refresh = st.sidebar.button("🔄 Refresh all data")


# ---------------------------------------------------------------- main
if not wl:
    st.info("Add tickers from the sidebar to populate the watchlist.")
    st.stop()

# decide whether to (re)fetch
need_fetch = refresh or "data" not in st.session_state

if need_fetch:
    with st.spinner("Fetching filings, news and quotes …"):
        data = {}
        for t in wl:
            c, name = (cik_map.get(t.upper(), {}).get("cik"), None)
            entry = cik_map.get(t.upper())
            name = entry["name"] if entry else t
            c = entry["cik"] if entry else None
            q = quote_for(t)
            n = news_for(t) if c else []
            f = recent_filings(c, selected_types, 20) if c else []
            data[t] = {
                "name": name,
                "cik": c,
                "quote": q,
                "news": n,
                "filings": f,
            }
    st.session_state.data = data
    st.session_state.fetch_time = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
else:
    data = st.session_state.data

fetch_time = st.session_state.get("fetch_time", "—")
st.sidebar.caption(f"Last refresh: {fetch_time}")

st.divider()

# ---------------------------------------------------------------- cards
for t in wl:
    d = data.get(t, {})
    name = d.get("name", t)
    q = d.get("quote", {})
    news = d.get("news", [])
    filings = d.get("filings", [])
    c = d.get("cik")

    missing_cik = c is None

    with st.expander(f"**{t}**  —  {name}", expanded=True):
        # status banner if EDGAR lookup failed
        if missing_cik:
            st.warning(
                f"'{t}' was not found in SEC EDGAR's company ticker list. "
                "Filings and news will not be shown. Remove it or verify the symbol."
            )
            continue

        # -- quote row
        price = q.get("price")
        prev = q.get("previous_close")
        change = q.get("change")
        change_pct = q.get("change_pct")
        mcap = q.get("market_cap")
        currency = q.get("currency", "USD")
        qname = q.get("name")

        c1, c2, c3, c4 = st.columns(4)
        if price is not None:
            c1.metric("Price", f"{price:,.2f} {currency}")
        else:
            c1.metric("Price", "—")

        if change is not None and prev:
            c2.metric("Day Change", f"{change:+,.2f} {currency}", delta=f"{change_pct:+.2f}%")
        else:
            c2.metric("Day Change", "—")

        c3.metric("Market Cap", _fmt_mcap(mcap))

        # company name from Yahoo (can differ slightly from SEC) — show as a caption
        if qname and qname != name:
            st.caption(f"Yahoo name: {qname}")

        st.divider()

        # -- news
        st.subheader("📰 Recent News")
        st.markdown(_news_list(news))

        st.divider()

        # -- filings
        st.subheader("📄 Recent SEC Filings")
        st.markdown(_filings_table(filings))
        st.caption(
            "All filings link to the official SEC EDGAR filing index. "
            f"Source: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={c}"
        )

st.divider()
st.caption(
    "Data sources & limitations: SEC EDGAR filings are authoritative and free. "
    "Yahoo Finance news/quotes are provided by yfinance and may be delayed or "
    "unavailable for some tickers. This is not financial advice."
)
