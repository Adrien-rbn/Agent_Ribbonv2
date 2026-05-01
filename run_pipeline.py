import os
import json
import re
import time
import shutil
import logging
import hashlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import pandas as pd
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

try:
    from tqdm.auto import tqdm
except ImportError:
    def tqdm(x, **kw):
        return x

# ---------------------------------------------------------------------------
# Secrets / API config
# ---------------------------------------------------------------------------
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "02931f08-788d-4c1d-9894-60904afd95fa")
NEWSAPI_ENDPOINT = "https://eventregistry.org/api/v1/article/getArticles"

# ---------------------------------------------------------------------------
# Date window: look back several days so there is always coverage
# ---------------------------------------------------------------------------
LOOK_BACK_DAYS = 3  # scan the last N days, ending yesterday
TODAY_UTC = datetime.now(timezone.utc).date()
YESTERDAY_UTC = TODAY_UTC - timedelta(days=1)
LOOK_BACK_START = TODAY_UTC - timedelta(days=LOOK_BACK_DAYS)
ANALYSIS_DATE = YESTERDAY_UTC.isoformat()
FROM_ISO = f"{LOOK_BACK_START.isoformat()}T00:00:00Z"
TO_ISO = f"{YESTERDAY_UTC.isoformat()}T23:59:59Z"

# ---------------------------------------------------------------------------
# Folder layout
# ---------------------------------------------------------------------------
BASE_DIR = Path.cwd()
DATA_RAW = BASE_DIR / "data" / "raw"
DATA_PROCESSED = BASE_DIR / "data" / "processed"
OUTPUTS_DIR = BASE_DIR / "outputs"
for d in (DATA_RAW, DATA_PROCESSED, OUTPUTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ribbon_news_agent")

print(f"Analysis date (yesterday, UTC): {ANALYSIS_DATE}")
if not NEWSAPI_KEY:
    raise RuntimeError(
        "NEWSAPI_KEY is not set. Create a .env file with:\n"
        "  NEWSAPI_KEY=your_key_here\n"
        "Get a free key at https://newsapi.org/register (NewsAPI.ai)"
    )
print(f"NewsAPI key present: {bool(NEWSAPI_KEY)}")
print(f"trafilatura available: {HAS_TRAFILATURA}")
print(f"Output folders ready: {DATA_RAW}, {DATA_PROCESSED}, {OUTPUTS_DIR}")


RIBBON_PROFILE = {
    "company_name": "Ribbon Communications",
    "tickers": ["RBBN"],
    "aliases": ["Ribbon Communications", "Ribbon Comms", "Ribbon"],
    "sector": "telecoms",
    "segments": [
        {
            "name": "Cloud & Edge",
            "products": [
                "session border controller", "SBC", "cloud communications",
                "voice over IP", "VoIP", "unified communications", "UCaaS",
                "cloud-native communications", "edge communications",
                "secure communications", "SIP trunking", "voice security",
            ],
            "customers": [
                "operators", "service providers", "enterprises",
                "government", "defense", "federal agencies",
            ],
        },
        {
            "name": "IP Optical",
            "products": [
                "IP optical", "optical networking", "coherent optics", "DWDM",
                "packet-optical", "transport network", "metro optical",
                "long-haul optical", "IP routing", "optical transport",
                "400G", "800G", "open optical",
            ],
            "customers": [
                "mobile operators", "tier-1 operators", "data centers",
                "hyperscalers", "cloud providers", "government",
                "wholesale carriers",
            ],
        },
    ],
    "geographies": [
        "US", "United States", "U.S.", "America",
        "EMEA", "Europe", "European Union", "EU", "Middle East", "Africa",
        "Asia Pacific", "APAC", "Asia-Pacific",
    ],
    "distribution": [
        "direct sales", "system integrator", "system integrators",
        "distributor", "distributors", "reseller", "resellers",
        "operator partner", "channel partner", "VAR",
    ],
    "competitors": [
        "Cisco", "Juniper", "Ciena", "Nokia", "Ericsson",
        "Infinera", "ADTRAN", "Mavenir", "Metaswitch", "Oracle Communications",
    ],
}

print(f"Profile loaded: {RIBBON_PROFILE['company_name']}")
print(f"Segments: {[s['name'] for s in RIBBON_PROFILE['segments']]}")


SOURCE_ALLOWLIST = {
    # Major business / financial media
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com",
    "marketwatch.com", "barrons.com", "forbes.com", "businessinsider.com",
    "axios.com", "economist.com", "nytimes.com", "washingtonpost.com",
    # Telecom / networking trade media
    "lightreading.com", "fiercewireless.com", "fiercetelecom.com",
    "telecoms.com", "rcrwireless.com", "totaltele.com", "telecomtv.com",
    "sdxcentral.com", "thefastmode.com", "capacitymedia.com",
    "developingtelecoms.com", "mobileworldlive.com", "networkworld.com",
    # General tech
    "theverge.com", "techcrunch.com", "arstechnica.com", "zdnet.com",
    "techradar.com", "wired.com",
    # Press release wires (high signal for contracts and product news)
    "prnewswire.com", "businesswire.com", "globenewswire.com",
    # Regulatory / official
    "fcc.gov", "ec.europa.eu", "sec.gov",
    # Ribbon itself
    "ribboncommunications.com",
}

print(f"Allowlist size: {len(SOURCE_ALLOWLIST)} domains")


def build_queries(profile: dict) -> list[tuple[str, list[str]]]:
    """Return a list of (label, [keywords]) pairs for NewsAPI.ai queries.

    Each keyword list is sent as a single OR-query to EventRegistry's
    POST /api/v1/article/getArticles endpoint.
    """
    queries = []

    # 1. Direct company monitoring
    # Use specific aliases only — bare "Ribbon" pulls unrelated noise
    direct_kws = [
        a for a in profile["aliases"] + profile["tickers"]
        if a.lower() != "ribbon"
    ]
    queries.append(("direct_company", direct_kws))

    # 2. Cloud & Edge segment products
    queries.append(("segment_cloud_and_edge", [
        "session border controller", "SBC", "VoIP", "UCaaS",
        "SIP trunking", "cloud communications", "unified communications",
    ]))

    # 3. IP Optical segment products
    queries.append(("segment_ip_optical", [
        "IP optical", "coherent optics", "DWDM", "packet-optical",
        "optical transport", "optical networking", "400G", "800G",
    ]))

    # 4. Market / customer drivers
    queries.append(("market_drivers", [
        "telecom operator", "mobile operator", "tier-1 carrier",
        "5G deployment", "network modernization", "fiber deployment",
        "capex telecom",
    ]))

    # 5. Competitor monitoring
    queries.append(("competitors", [
        "Cisco networking", "Juniper networks", "Ciena optical",
        "Nokia optical", "Ericsson network", "Mavenir", "Oracle Communications",
    ]))

    # 6. Regulatory / procurement
    queries.append(("regulatory", [
        "FCC", "BEAD program", "rip and replace telecom",
        "Open RAN", "Huawei ban", "ZTE ban",
    ]))

    return queries


# Preview the queries that will be sent
QUERIES = build_queries(RIBBON_PROFILE)
for label, kws in QUERIES:
    print(f"[{label}] {kws[:4]}{'...' if len(kws) > 4 else ''}")


def fetch_articles(queries: list[tuple[str, list[str]]],
                   from_iso: str,
                   to_iso: str,
                   api_key: str) -> list[dict]:
    """Fetch articles from NewsAPI.ai (EventRegistry) for each query bucket.

    Uses POST /api/v1/article/getArticles with keyword OR-matching.
    Full article body is returned by the API — hydration is skipped for rows
    where extraction_status == 'ok_from_api'.
    """
    # EventRegistry uses YYYY-MM-DD, not full ISO datetimes
    date_start = from_iso[:10]
    date_end = to_iso[:10]

    all_articles = []

    for label, keywords in queries:
        body = {
            "apiKey": api_key,
            "keyword": keywords,
            "keywordOper": "or",
            "dateStart": date_start,
            "dateEnd": date_end,
            "lang": "eng",
            "articlesCount": 50,
            "articlesSortBy": "date",
            "resultType": "articles",
        }
        try:
            resp = requests.post(NEWSAPI_ENDPOINT, json=body, timeout=30)
        except requests.exceptions.RequestException as exc:
            logger.error(f"[{label}] network error: {exc}")
            continue

        if resp.status_code == 401:
            payload = resp.json()
            raise RuntimeError(
                f"NewsAPI.ai authentication failed: {payload.get('message', resp.text[:200])}\n"
                "Set NEWSAPI_KEY in your .env file and re-run from cell 2."
            )
        if resp.status_code != 200:
            logger.warning(f"[{label}] HTTP {resp.status_code}: {resp.text[:200]}")
            continue

        payload = resp.json()
        raw_path = DATA_RAW / f"{ANALYSIS_DATE}_{label}.json"
        raw_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

        results = (payload.get("articles") or {}).get("results") or []
        for art in results:
            body_text = art.get("body") or ""
            extraction_status = "ok_from_api" if len(body_text) > 200 else "thin"
            source = art.get("source") or {}
            mapped = {
                "title": art.get("title") or "",
                "description": body_text[:500],
                "url": art.get("url") or "",
                "publishedAt": art.get("dateTimePub") or "",
                "content": body_text,
                "article_text": body_text,
                "source": {"id": None, "name": source.get("title") or ""},
                "extraction_status": extraction_status,
                "_query_label": label,
            }
            all_articles.append(mapped)

        logger.info(
            f"[{label}] received {len(results)} articles ({date_start} \u2192 {date_end})"
        )
        time.sleep(0.5)

    return all_articles


def deduplicate_articles(articles: list[dict]) -> list[dict]:
    """Deduplicate by URL, merging _query_label values."""
    by_url: dict[str, dict] = {}
    for art in articles:
        url = (art.get("url") or "").strip()
        if not url:
            continue
        if url in by_url:
            existing_labels = by_url[url].get("_query_labels", [])
            new_label = art.get("_query_label")
            if new_label and new_label not in existing_labels:
                existing_labels.append(new_label)
            by_url[url]["_query_labels"] = existing_labels
        else:
            art["_query_labels"] = [art.get("_query_label")]
            by_url[url] = art
    return list(by_url.values())


# Run the fetch
raw_articles = fetch_articles(QUERIES, FROM_ISO, TO_ISO, NEWSAPI_KEY)
articles = deduplicate_articles(raw_articles)
print(f"\nFetched {len(raw_articles)} raw, {len(articles)} unique articles ({FROM_ISO[:10]} \u2192 {TO_ISO[:10]})")


def extract_article_text(url: str, timeout: int = 15) -> tuple[str, str]:
    """Return (extraction_status, text). Status is one of:

      ok            — trafilatura returned >200 chars
      fallback      — BeautifulSoup paragraph harvest returned >200 chars
      thin          — content fetched but too short to be useful
      http_<code>   — non-200 HTTP response
      timeout       — request timed out
      error         — any other exception
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; RibbonNewsAgent/0.1; "
            "+research-prototype) "
        )
    }

    # 1) trafilatura path
    if HAS_TRAFILATURA:
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(
                    downloaded, include_comments=False, include_tables=False
                )
                if text and len(text) > 200:
                    return "ok", text
        except Exception:
            pass

    # 2) requests + BeautifulSoup fallback
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        return "timeout", ""
    except requests.exceptions.RequestException:
        return "error", ""

    if resp.status_code != 200:
        return f"http_{resp.status_code}", ""

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer",
                         "header", "aside", "form"]):
            tag.decompose()
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
        text = "\n".join(p for p in paragraphs if len(p) > 40)
        if len(text) > 200:
            return "fallback", text
        return "thin", text
    except Exception:
        return "error", ""


def hydrate_articles(articles: list[dict]) -> list[dict]:
    """Add 'article_text' and 'extraction_status' fields to each article.

    Skips articles where extraction_status is already 'ok_from_api'
    (full body was returned directly by the NewsAPI.ai response).
    """
    for art in tqdm(articles, desc="Extracting articles"):
        if art.get("extraction_status") == "ok_from_api":
            continue  # full body already in article_text from API
        url = art.get("url")
        if not url:
            art["extraction_status"] = "no_url"
            art["article_text"] = ""
            continue
        status, text = extract_article_text(url)
        art["extraction_status"] = status
        art["article_text"] = text
        time.sleep(0.3)
    return articles


articles = hydrate_articles(articles)

# Quick extraction-quality summary
status_counts: dict[str, int] = {}
for a in articles:
    status_counts[a["extraction_status"]] = status_counts.get(
        a["extraction_status"], 0) + 1
print("\nExtraction status distribution:")
for k, v in sorted(status_counts.items(), key=lambda x: -x[1]):
    print(f"  {k:15s} {v}")


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _haystack(article: dict) -> str:
    """Concatenate title + description + extracted text into one lowercase string."""
    return " ".join([
        (article.get("title") or ""),
        (article.get("description") or ""),
        (article.get("content") or ""),
        (article.get("article_text") or "")[:5000],
    ]).lower()


ACTION_TERMS = [
    "contract", "deployment", "deploy", "rollout", "5g", "open ran",
    "modernization", "modernize", "upgrade", "partnership", "acquisition",
    "merger", "tender", "rfp", "procurement", "expansion",
]


def score_relevance(article: dict, profile: dict) -> dict:
    """Return a dict with relevance score, matches, and a short reason string."""
    text = _haystack(article)
    matches = {
        "company": [],
        "segments": [],
        "products": [],
        "customers": [],
        "geographies": [],
        "distribution": [],
        "competitors": [],
    }
    score = 0.0
    reasons: list[str] = []

    # 1) Direct company / ticker mention
    for alias in profile["aliases"] + profile["tickers"]:
        if alias.lower() in text:
            matches["company"].append(alias)
            score += 5.0
            reasons.append(f"direct mention: {alias}")
            break

    # 2) Segment products + customers
    seg_product_hits = 0
    for segment in profile["segments"]:
        for prod in segment["products"]:
            if prod.lower() in text:
                if prod not in matches["products"]:
                    matches["products"].append(prod)
                if segment["name"] not in matches["segments"]:
                    matches["segments"].append(segment["name"])
                seg_product_hits += 1
                if seg_product_hits <= 5:  # cap weight
                    score += 1.5
                    reasons.append(f"product: {prod} ({segment['name']})")
        for cust in segment["customers"]:
            if cust.lower() in text and cust not in matches["customers"]:
                matches["customers"].append(cust)
                score += 0.7

    # 3) Geographies
    for geo in profile["geographies"]:
        if geo.lower() in text and geo not in matches["geographies"]:
            matches["geographies"].append(geo)
            score += 0.3

    # 4) Distribution
    for dist in profile["distribution"]:
        if dist.lower() in text and dist not in matches["distribution"]:
            matches["distribution"].append(dist)
            score += 0.4

    # 5) Competitors
    for comp in profile["competitors"]:
        if comp.lower() in text and comp not in matches["competitors"]:
            matches["competitors"].append(comp)
            score += 0.5
            reasons.append(f"competitor: {comp}")

    # 6) Action terms (capped)
    action_hits = 0
    for term in ACTION_TERMS:
        if term in text:
            action_hits += 1
            if action_hits <= 3:
                score += 0.2

    # 7) Source allowlist bonus / off-list penalty
    domain = _domain(article.get("url") or "")
    on_list = any(domain.endswith(d) for d in SOURCE_ALLOWLIST)
    if on_list:
        score += 0.5
    else:
        score -= 0.3

    if not reasons:
        reasons.append("only weak signals (geography / action terms)")

    return {
        "score": round(score, 2),
        "matches": matches,
        "reason": "; ".join(reasons[:6]),
        "domain": domain,
        "on_allowlist": on_list,
    }


POSITIVE_TERMS = [
    "contract win", "wins contract", "awarded", "selects", "selected",
    "chooses", "chose", "adopts", "deployment", "deploy", "rollout",
    "expansion", "expand", "growth", "partnership", "partner with",
    "investment", "invest", "launch", "introduces", "modernization",
    "modernize", "upgrade", "innovation", "new customer", "5g rollout",
    "record revenue", "beats estimate",
]
NEGATIVE_TERMS = [
    "layoff", "lay off", "decline", "loss", "lawsuit", "breach", "outage",
    "ban", "restriction", "sanction", "tariff", "downturn", "delay",
    "cancel", "cancellation", "disruption", "shortage", "fine", "penalty",
    "investigation", "vulnerability", "exploit", "hack", "fraud",
    "misses estimate", "warning", "downgrade",
]


def estimate_impact(article: dict, relevance: dict) -> dict:
    """Return {label, score, confidence, positive_signals, negative_signals}."""
    text = _haystack(article)
    pos = sum(1 for t in POSITIVE_TERMS if t in text)
    neg = sum(1 for t in NEGATIVE_TERMS if t in text)
    raw = pos - neg

    if raw >= 3:
        score = 2
    elif raw >= 1:
        score = 1
    elif raw <= -3:
        score = -2
    elif raw <= -1:
        score = -1
    else:
        score = 0

    if score > 0:
        label = "positive"
    elif score < 0:
        label = "negative"
    elif pos > 0 and neg > 0:
        label = "mixed"
    else:
        label = "neutral"

    has_company = bool(relevance["matches"]["company"])
    rel_score = relevance["score"]
    if has_company and rel_score >= 5.0:
        confidence = "high"
    elif rel_score >= 3.0:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "label": label,
        "score": score,
        "confidence": confidence,
        "positive_signals": pos,
        "negative_signals": neg,
    }


def _article_id(url: str, published_at: str) -> str:
    base = f"{url}|{published_at}".encode("utf-8")
    return hashlib.sha1(base).hexdigest()[:12]


def build_dataframe(articles: list[dict], profile: dict) -> pd.DataFrame:
    rows = []
    for art in articles:
        rel = score_relevance(art, profile)
        imp = estimate_impact(art, rel)

        kept = bool(rel["matches"]["company"]) or rel["score"] >= 3.0
        url = art.get("url") or ""
        published = art.get("publishedAt") or ""

        rows.append({
            "analysis_date": ANALYSIS_DATE,
            "article_id": _article_id(url, published),
            "title": art.get("title") or "",
            "source_name": (art.get("source") or {}).get("name") or "",
            "source_domain": rel["domain"],
            "published_at": published,
            "url": url,
            "extraction_status": art.get("extraction_status", "unknown"),
            "article_text": (art.get("article_text") or "")[:8000],
            "matched_company": ", ".join(rel["matches"]["company"]),
            "matched_segments": ", ".join(rel["matches"]["segments"]),
            "matched_products": ", ".join(rel["matches"]["products"][:8]),
            "matched_customers": ", ".join(rel["matches"]["customers"][:8]),
            "matched_geographies": ", ".join(rel["matches"]["geographies"][:6]),
            "matched_distribution": ", ".join(rel["matches"]["distribution"][:6]),
            "relevance_score": rel["score"],
            "relevance_reason": rel["reason"],
            "impact_label": imp["label"],
            "impact_score": imp["score"],
            "impact_confidence": imp["confidence"],
            "supporting_sources": ", ".join(art.get("_query_labels", [])),
            "kept_for_digest": kept,
            "on_allowlist": rel["on_allowlist"],
            "positive_signals": imp["positive_signals"],
            "negative_signals": imp["negative_signals"],
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(
            ["kept_for_digest", "relevance_score"],
            ascending=[False, False],
        ).reset_index(drop=True)
    return df


df = build_dataframe(articles, RIBBON_PROFILE)
print(f"DataFrame shape: {df.shape}")
if not df.empty:
    print(f"Kept for digest: {int(df['kept_for_digest'].sum())}")
    print(f"Borderline (1.5 <= score < 3.0): "
          f"{int(((df['relevance_score'] >= 1.5) & (df['relevance_score'] < 3.0)).sum())}")
    display_cols = ["title", "source_name", "relevance_score",
                    "impact_label", "impact_score", "kept_for_digest"]
    df.head(10)[display_cols]


def save_outputs(df: pd.DataFrame) -> tuple[Path, Path]:
    csv_path = DATA_PROCESSED / f"{ANALYSIS_DATE}_articles.csv"
    json_path = DATA_PROCESSED / f"{ANALYSIS_DATE}_articles.json"
    df.to_csv(csv_path, index=False)
    df.to_json(json_path, orient="records", indent=2, force_ascii=False)
    logger.info(f"Saved {len(df)} rows to {csv_path}")
    logger.info(f"Saved {len(df)} rows to {json_path}")
    return csv_path, json_path


csv_path, json_path = save_outputs(df)


def _fmt_matches(row: pd.Series) -> str:
    parts = []
    for label, col in [
        ("Company", "matched_company"),
        ("Segments", "matched_segments"),
        ("Products", "matched_products"),
        ("Customers", "matched_customers"),
        ("Geos", "matched_geographies"),
        ("Distribution", "matched_distribution"),
    ]:
        val = row.get(col, "") or ""
        if val:
            parts.append(f"**{label}:** {val}")
    return "  \n".join(parts) if parts else "_no structured matches_"


def _executive_summary(kept_df: pd.DataFrame) -> list[str]:
    """Produce 3-6 short bullets summarising the day."""
    bullets: list[str] = []
    if kept_df.empty:
        return ["No high-confidence items for Ribbon yesterday."]

    direct = kept_df[kept_df["matched_company"] != ""]
    if not direct.empty:
        bullets.append(
            f"{len(direct)} article(s) directly mention Ribbon / RBBN."
        )

    pos = int((kept_df["impact_score"] > 0).sum())
    neg = int((kept_df["impact_score"] < 0).sum())
    neu = int((kept_df["impact_score"] == 0).sum())
    bullets.append(
        f"Impact mix: {pos} positive, {neg} negative, {neu} neutral/mixed."
    )

    seg_counts: dict[str, int] = {}
    for segs in kept_df["matched_segments"]:
        for s in [s.strip() for s in (segs or "").split(",") if s.strip()]:
            seg_counts[s] = seg_counts.get(s, 0) + 1
    if seg_counts:
        top_segs = ", ".join(
            f"{s} ({n})" for s, n in
            sorted(seg_counts.items(), key=lambda x: -x[1])[:3]
        )
        bullets.append(f"Most-mentioned segments: {top_segs}.")

    geo_counts: dict[str, int] = {}
    for geos in kept_df["matched_geographies"]:
        for g in [g.strip() for g in (geos or "").split(",") if g.strip()]:
            geo_counts[g] = geo_counts.get(g, 0) + 1
    if geo_counts:
        top_geos = ", ".join(
            f"{g} ({n})" for g, n in
            sorted(geo_counts.items(), key=lambda x: -x[1])[:3]
        )
        bullets.append(f"Geographic spread: {top_geos}.")

    top = kept_df.iloc[0]
    bullets.append(
        f"Top item: \"{top['title']}\" "
        f"({top['source_name']}, impact {top['impact_label']} "
        f"{top['impact_score']:+d}, conf {top['impact_confidence']})."
    )
    return bullets[:6]


def generate_digest(df: pd.DataFrame) -> Path:
    digest_path = OUTPUTS_DIR / f"{ANALYSIS_DATE}_digest.md"
    kept = df[df["kept_for_digest"]] if not df.empty else df
    borderline = (
        df[(df["kept_for_digest"] == False) &
           (df["relevance_score"] >= 1.5) &
           (df["relevance_score"] < 3.0)]
        if not df.empty else df
    )

    lines: list[str] = []
    lines.append("# Daily Intelligence Brief — Ribbon Communications")
    lines.append("")
    lines.append(f"**Analysis date:** {ANALYSIS_DATE} (UTC)")
    lines.append(f"**Articles considered:** {len(df)}  ")
    lines.append(f"**Kept for digest:** {len(kept)}  ")
    lines.append(f"**Borderline watchlist:** {len(borderline)}")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    for b in _executive_summary(kept):
        lines.append(f"- {b}")
    lines.append("")

    if kept.empty:
        lines.append("## No high-confidence items today")
        lines.append("")
        lines.append(
            "No article met the relevance threshold "
            "(direct Ribbon mention or relevance_score >= 3.0)."
        )
    else:
        lines.append("## Kept articles")
        lines.append("")
        for _, row in kept.iterrows():
            lines.append(f"### {row['title']}")
            lines.append("")
            lines.append(f"- **Source:** {row['source_name']} "
                         f"({row['source_domain']})")
            lines.append(f"- **Published:** {row['published_at']}")
            lines.append(f"- **URL:** {row['url']}")
            lines.append(f"- **Impact:** {row['impact_label']} "
                         f"(score {row['impact_score']:+d}, "
                         f"confidence {row['impact_confidence']})")
            lines.append(f"- **Relevance score:** {row['relevance_score']}")
            lines.append(f"- **Why it matters for Ribbon:** "
                         f"{row['relevance_reason']}")
            lines.append("")
            lines.append(_fmt_matches(row))
            lines.append("")
            lines.append(f"_Discovered via: {row['supporting_sources']}_")
            lines.append("")
            lines.append("---")
            lines.append("")

    if not borderline.empty:
        lines.append("## Borderline watchlist")
        lines.append("")
        lines.append("Items that did not meet the kept threshold but are "
                     "worth a quick scan:")
        lines.append("")
        for _, row in borderline.iterrows():
            lines.append(f"- **{row['title']}** — {row['source_name']} "
                         f"(rel {row['relevance_score']}, "
                         f"impact {row['impact_label']}). "
                         f"{row['url']}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Generated by the Ribbon news-intelligence MVP. "
                 "Heuristic scoring; treat as triage, not analysis._")

    digest_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Digest written: {digest_path}")
    return digest_path


def generate_selection_log(df: pd.DataFrame) -> Path:
    log_path = OUTPUTS_DIR / f"{ANALYSIS_DATE}_selection_log.md"
    lines: list[str] = []
    lines.append("# Selection Log — Ribbon Communications News Agent")
    lines.append("")
    lines.append(f"**Analysis date:** {ANALYSIS_DATE} (UTC)")
    lines.append(f"**Total articles considered:** {len(df)}")
    lines.append("")
    lines.append("Each entry below shows the article, the matches found, "
                 "the relevance score, the impact estimate, and whether the "
                 "article was **kept** or **rejected** for the digest.")
    lines.append("")
    lines.append("Selection rule: kept if direct Ribbon/RBBN mention OR "
                 "relevance_score >= 3.0.")
    lines.append("")

    if df.empty:
        lines.append("_No articles were returned by NewsAPI for this date._")
        log_path.write_text("\n".join(lines), encoding="utf-8")
        return log_path

    for _, row in df.iterrows():
        decision = "KEPT" if row["kept_for_digest"] else "REJECTED"
        lines.append(f"## [{decision}] {row['title']}")
        lines.append("")
        lines.append(f"- **Article ID:** `{row['article_id']}`")
        lines.append(f"- **Source:** {row['source_name']} "
                     f"({row['source_domain']}) — "
                     f"{'on allowlist' if row['on_allowlist'] else 'off allowlist'}")
        lines.append(f"- **Published:** {row['published_at']}")
        lines.append(f"- **URL:** {row['url']}")
        lines.append(f"- **Discovered via queries:** "
                     f"{row['supporting_sources']}")
        lines.append(f"- **Extraction status:** {row['extraction_status']}")
        lines.append(f"- **Relevance score:** {row['relevance_score']}")
        lines.append(f"- **Reason:** {row['relevance_reason']}")
        lines.append(f"- **Impact:** {row['impact_label']} "
                     f"(score {row['impact_score']:+d}, "
                     f"confidence {row['impact_confidence']}, "
                     f"+{row['positive_signals']}/-{row['negative_signals']})")
        lines.append("")
        lines.append(_fmt_matches(row))
        lines.append("")
        if not row["kept_for_digest"]:
            if not row["matched_company"]:
                if row["relevance_score"] < 3.0:
                    lines.append(
                        f"**Rejection reason:** no direct Ribbon mention "
                        f"and relevance score "
                        f"{row['relevance_score']} below threshold (3.0)."
                    )
        else:
            if row["matched_company"]:
                lines.append("**Kept reason:** direct Ribbon/RBBN mention.")
            else:
                lines.append(
                    f"**Kept reason:** relevance score "
                    f"{row['relevance_score']} >= threshold (3.0)."
                )
        lines.append("")
        lines.append("---")
        lines.append("")

    log_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Selection log written: {log_path}")
    return log_path


def open_in_vscode(*paths: Path) -> None:
    """Best-effort: open each file as a new VS Code editor tab.

    Silently no-ops if the `code` CLI is not on PATH (e.g. headless run).
    """
    code_cli = shutil.which("code")
    if not code_cli:
        logger.info("`code` CLI not found on PATH; skipping VS Code open. "
                    "Run 'Shell Command: Install \"code\" command in PATH' "
                    "from the VS Code command palette to enable this.")
        return
    for p in paths:
        try:
            subprocess.run([code_cli, "--reuse-window", str(p)],
                           check=False, timeout=5)
            logger.info(f"Opened in VS Code: {p}")
        except Exception as exc:
            logger.warning(f"Could not open {p} in VS Code: {exc}")


digest_path = generate_digest(df)
log_path = generate_selection_log(df)
open_in_vscode(digest_path, log_path)

print(f"\nDigest: {digest_path}")
print(f"Selection log: {log_path}")


# Quick at-a-glance recap of the run
if not df.empty:
    summary = {
        "analysis_date": ANALYSIS_DATE,
        "articles_total": int(len(df)),
        "articles_kept": int(df["kept_for_digest"].sum()),
        "direct_ribbon_mentions": int((df["matched_company"] != "").sum()),
        "extraction_ok_or_fallback": int(df["extraction_status"].isin(
            ["ok", "fallback"]).sum()),
        "impact_distribution": df["impact_label"].value_counts().to_dict(),
        "digest_path": str(digest_path),
        "selection_log_path": str(log_path),
        "csv_path": str(csv_path),
    }
else:
    summary = {
        "analysis_date": ANALYSIS_DATE,
        "articles_total": 0,
        "note": "NewsAPI returned no articles. Check the API key, "
                "the date window, or query syntax.",
    }
print(json.dumps(summary, indent=2, default=str))
