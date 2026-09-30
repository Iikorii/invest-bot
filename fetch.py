"""Data layer for the Investment Research Bot.

Free sources:
  - SEC EDGAR (filings, company names, CIK lookup) -- no API key
  - Yahoo Finance via yfinance (news + quote data) -- no API key

Both have rate limits; SEC recommends <10 req/sec and a descriptive User-Agent.
"""
import json
import os
import time
from datetime import datetime, timezone

import requests
import yfinance as yf

SEC_HEADERS = {"User-Agent": "InvestmentResearchBot/1.0 (research@example.com)"}

_HERE = os.path.dirname(os.path.abspath(__file__))
CIK_CACHE = os.path.join(_HERE, "cik_map.json")
WATCHLIST_FILE = os.path.join(_HERE, "watchlist.json")

# Filing forms we surface, with a readable label.
FILING_LABELS = {
    "8-K": "Current Report",
    "10-K": "Annual Report",
    "10-Q": "Quarterly Report",
    "10-K/A": "Amended Annual Report",
    "10-Q/A": "Amended Quarterly Report",
    "SC 13D": "Beneficial Ownership (13D)",
    "SC 13G": "Beneficial Ownership (13G)",
    "3": "Insider Form 3",
    "4": "Insider Form 4",
    "5": "Insider Form 5",
    "DEF 14A": "Proxy Statement",
    "S-1": "IPO Registration",
    "S-4": "Registration (S-4)",
    "PRE 14A": "Proxy (pre-filing)",
    "NRF 14": "Proxy (non-RFC)",
}

# Default watchlist when the config file is missing.
DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "BRK-B", "V", "JPM",
]


# ---------------------------------------------------------------------------
# SEC EDGAR helpers
# ---------------------------------------------------------------------------

def load_cik_map(force=False):
    """Return {TICKER: {cik, name}} from SEC's company_tickers.json.

    The result is cached locally for 24h to avoid hammering EDGAR on every
    dashboard refresh (watchlists rarely change that fast).
    """
    fresh = (not force) and os.path.exists(CIK_CACHE) and (
        time.time() - os.path.getmtime(CIK_CACHE) < 86400
    )
    if fresh:
        with open(CIK_CACHE) as f:
            return json.load(f)

    resp = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=SEC_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json()
    mapping = {}
    for _k, v in raw.items():
        ticker = str(v.get("ticker", "")).upper()
        if ticker:
            mapping[ticker] = {
                "cik": str(v.get("cik_str", "")).zfill(10),
                "name": v.get("title") or ticker,
            }

    # atomic write
    tmp = CIK_CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(mapping, f)
    os.replace(tmp, CIK_CACHE)
    return mapping


def lookup(ticker, mapping=None):
    """Return (cik_10digit, company_name) for a ticker, or (None, None)."""
    if mapping is None:
        mapping = load_cik_map()
    e = mapping.get(ticker.upper())
    return (e["cik"], e["name"]) if e else (None, None)


def company_name(ticker, mapping=None):
    _, name = lookup(ticker, mapping)
    return name


def cik(ticker, mapping=None):
    c, _ = lookup(ticker, mapping)
    return c


def recent_filings(cik_10digit, form_types=None, limit=25):
    """Return the most recent filings for a CIK.

    Each item:
        date, form, label, accession, acc_nodash, primary_doc,
        index_url, doc_url
    """
    url = f"https://data.sec.gov/submissions/CIK{cik_10digit}.json"
    resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    cik_int = str(int(cik_10digit))  # strip leading zeros

    out = []
    for frm, dt, acc, doc in zip(forms, dates, accessions, primary_docs):
        if form_types and frm not in form_types:
            continue
        acc_nodash = acc.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"
        out.append({
            "date": dt,
            "form": frm,
            "label": FILING_LABELS.get(frm, frm),
            "accession": acc,
            "acc_nodash": acc_nodash,
            "primary_doc": doc,
            "index_url": base,
            "doc_url": f"{base}{doc}",
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Yahoo Finance helpers
# ---------------------------------------------------------------------------

def news_for(ticker):
    """Recent news items for *ticker* from Yahoo Finance.

    Returns a list of dicts: title, publisher, url, date, content_type, source.
    On failure returns a single dict with an ``error`` key.
    """
    try:
        items = yf.Ticker(ticker).news or []
    except Exception as exc:
        return [{"error": f"yfinance error: {exc}"}]

    out = []
    for it in items:
        c = it.get("content", {})
        title = c.get("title") or "(no title)"
        provider = c.get("provider", {})
        publisher = provider.get("displayName", "")
        ctu = c.get("clickThroughUrl") or c.get("canonicalUrl") or {}
        url = ctu.get("url", "")
        out.append({
            "title": title,
            "publisher": publisher,
            "url": url,
            "date": c.get("pubDate", "")[:10] if c.get("pubDate") else "",
            "content_type": c.get("contentType", "STORY") or "STORY",
            "source": provider.get("sourceId", ""),
        })
    return out


def quote_for(ticker):
    """Current quote for *ticker*.

    Returns: price, previous_close, change, change_pct, market_cap,
              currency, name
    """
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    prev = info.get("previousClose")
    change = info.get("regularMarketChange")
    change_pct = info.get("regularMarketChangePercent")
    mcap = info.get("marketCap")
    currency = info.get("currency", "USD")
    name = info.get("longName") or info.get("shortName") or ticker
    return {
        "price": price,
        "previous_close": prev,
        "change": change,
        "change_pct": change_pct,
        "market_cap": mcap,
        "currency": currency,
        "name": name,
    }


# ---------------------------------------------------------------------------
# Watchlist persistence
# ---------------------------------------------------------------------------

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return list(DEFAULT_WATCHLIST)


def save_watchlist(tickers):
    tmp = WATCHLIST_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tickers, f, indent=2)
    os.replace(tmp, WATCHLIST_FILE)
