"""Throwaway probe of the Web of Science Starter API against a live key.

Verifies auth, dumps the real response shape, and (optionally) compares a
WoS topic search against an OpenAlex search for the same terms. Reads
WOS_STARTER_API_KEY from .env. Deliberately frugal with calls — the trial
tier is rate-limited.

Usage:
    pixi run python scripts/explore_wos.py probe        # 1 WoS call, dump shapes
    pixi run python scripts/explore_wos.py compare       # WoS + OpenAlex counts/overlap
"""

from __future__ import annotations

import json
import sys
import time

import httpx

from laglitsynth.dotenv import load_env_var

BASE = "https://api.clarivate.com/apis/wos-starter/v1"
QUERY = 'TS=("Lagrangian" AND "ocean" AND "particle tracking")'


def _client(key: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        headers={"X-ApiKey": key, "Accept": "application/json"},
        timeout=30.0,
    )


def probe(key: str) -> None:
    with _client(key) as c:
        r = c.get("/documents", params={"q": QUERY, "db": "WOS", "limit": 5, "page": 1})
        print(f"HTTP {r.status_code}", file=sys.stderr)
        print("Rate-limit headers:", file=sys.stderr)
        for h, v in r.headers.items():
            if "limit" in h.lower() or "remain" in h.lower() or "quota" in h.lower():
                print(f"  {h}: {v}", file=sys.stderr)
        r.raise_for_status()
        data = r.json()

        print("=== TOP-LEVEL KEYS ===")
        print(list(data.keys()))
        print("\n=== METADATA ===")
        print(json.dumps(data.get("metadata"), indent=2))

        hits = data.get("hits") or data.get("data") or []
        print(f"\n=== n records in page: {len(hits)} ===")
        if hits:
            print("\n=== FIRST RECORD (full JSON) ===")
            print(json.dumps(hits[0], indent=2, ensure_ascii=False))
            print("\n=== 'abstract' anywhere in first record? ===")
            blob = json.dumps(hits[0]).lower()
            print("contains 'abstract':", "abstract" in blob)


def compare(key: str) -> None:
    import pyalex

    oa_key = load_env_var("OPENALEX_API_KEY")
    if oa_key:
        pyalex.config.api_key = oa_key

    # --- WoS ---
    with _client(key) as c:
        r = c.get("/documents", params={"q": QUERY, "db": "WOS", "limit": 50, "page": 1})
        r.raise_for_status()
        data = r.json()
    wos_total = data.get("metadata", {}).get("total")
    hits = data.get("hits") or data.get("data") or []
    wos_dois = set()
    for h in hits:
        doi = (h.get("identifiers") or {}).get("doi")
        if doi:
            wos_dois.add(doi.lower().replace("https://doi.org/", ""))
    print(f"WoS total matches: {wos_total}; DOIs in first 50: {len(wos_dois)}")

    time.sleep(1)

    # --- OpenAlex: search the same free-text terms ---
    oa_search = "Lagrangian ocean particle tracking"
    oa_q = pyalex.Works().search(oa_search)
    oa_total = oa_q.count()
    print(f"OpenAlex search '{oa_search}' total: {oa_total}")

    oa_dois = set()
    for page in oa_q.select(["id", "doi", "title"]).paginate(per_page=50, n_max=50):
        for w in page:
            doi = w.get("doi")
            if doi:
                oa_dois.add(doi.lower().replace("https://doi.org/", ""))
    print(f"OpenAlex DOIs in first 50: {len(oa_dois)}")

    overlap = wos_dois & oa_dois
    print(f"\nDOI overlap (first-50 vs first-50): {len(overlap)}")
    print("Sample overlap:", list(overlap)[:5])
    print("WoS-only sample:", list(wos_dois - oa_dois)[:5])
    print("OpenAlex-only sample:", list(oa_dois - wos_dois)[:5])


def coverage(key: str) -> None:
    """Take WoS DOIs, look them up in OpenAlex, report coverage + abstracts."""
    import pyalex

    oa_key = load_env_var("OPENALEX_API_KEY")
    if oa_key:
        pyalex.config.api_key = oa_key

    # Pull two pages of WoS hits (up to 100) to get a decent DOI sample.
    wos_dois: list[str] = []
    with _client(key) as c:
        for page in (1, 2):
            r = c.get(
                "/documents",
                params={"q": QUERY, "db": "WOS", "limit": 50, "page": page},
            )
            r.raise_for_status()
            for h in r.json().get("hits", []):
                doi = (h.get("identifiers") or {}).get("doi")
                if doi:
                    wos_dois.append(doi.lower().replace("https://doi.org/", ""))
            time.sleep(0.3)

    wos_dois = sorted(set(wos_dois))
    print(f"WoS DOIs collected (first 2 pages): {len(wos_dois)}")

    # Look them up in OpenAlex by DOI (OR-joined filter), batches of 25.
    found = 0
    with_abstract = 0
    oa_titles: dict[str, str] = {}
    for i in range(0, len(wos_dois), 25):
        batch = wos_dois[i : i + 25]
        flt = "|".join(f"https://doi.org/{d}" for d in batch)
        results = (
            pyalex.Works()
            .filter(doi=flt)
            .select(["id", "doi", "title", "abstract_inverted_index"])
            .get(per_page=50)
        )
        for w in results:
            found += 1
            doi = (w.get("doi") or "").lower().replace("https://doi.org/", "")
            oa_titles[doi] = w.get("title") or ""
            if w.get("abstract_inverted_index"):
                with_abstract += 1
        time.sleep(0.3)

    print(f"Found in OpenAlex by DOI: {found} / {len(wos_dois)}")
    print(f"  of those, with abstract_inverted_index: {with_abstract}")
    missing = [d for d in wos_dois if d not in oa_titles]
    print(f"WoS DOIs NOT in OpenAlex: {len(missing)}")
    print("  sample missing:", missing[:5])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    api_key = load_env_var("WOS_STARTER_API_KEY")
    if not api_key:
        raise SystemExit("WOS_STARTER_API_KEY not found in .env")
    if mode == "probe":
        probe(api_key)
    elif mode == "compare":
        compare(api_key)
    elif mode == "coverage":
        coverage(api_key)
    else:
        raise SystemExit(f"unknown mode: {mode}")
