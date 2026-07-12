"""Event tag -> category mapping.

Built empirically from the 363 distinct tags observed across the 126 events
in the Gate A cache (freq>=2 gray-zone reviewed and signed off; freq==1 tags
are almost entirely named entities and are left unmapped by design). Two
judgment calls worth a DECISIONS.md line:

- "doge" -> Politics, not Crypto: the only example title carrying this tag
  is "Will DOGE balance the budget in 2025?" (Dept. of Government
  Efficiency), not Dogecoin. Verified from context, not assumed from the
  string.
- Deliberately left unmapped (falls through to "Other" by absence, same as
  any tag never seen): Hide From New, Featured, Pre-Market, Breaking News,
  Science, Pandemics, predictions, World, Tech, technology, internet, us —
  too generic or polysemous at this frequency (e.g. "Tech"/"internet" span
  both Crypto and Culture depending on context) to map safely.
"""

from __future__ import annotations

# tag -> category, exact match after case-fold. Precedence when an event
# has multiple mapped tags: Geopolitics > Crypto > Sports > Econ/Finance
# > Culture > Politics > Other.
CATEGORY_MAP: dict[str, str] = {
    # Geopolitics — checked first: specific & rare, shouldn't be swallowed by "Politics"
    "geopolitics": "Geopolitics", "middle east": "Geopolitics", "israel": "Geopolitics",
    "iran": "Geopolitics", "russia": "Geopolitics", "ukraine": "Geopolitics",
    "china": "Geopolitics", "war": "Geopolitics", "nato": "Geopolitics",
    "gaza": "Geopolitics", "hamas": "Geopolitics", "ceasefire": "Geopolitics",
    "international relations": "Geopolitics", "taiwan election": "Geopolitics",

    # Crypto
    "crypto": "Crypto", "cryptocurrency": "Crypto", "bitcoin": "Crypto",
    "ethereum": "Crypto", "defi": "Crypto", "nft": "Crypto", "blockchain": "Crypto",
    "airdrops": "Crypto", "crypto prices": "Crypto", "etf": "Crypto",
    "etf approval": "Crypto", "btc": "Crypto", "etfs": "Crypto",

    # Sports (collapse the league/sport variants into one bucket)
    "sports": "Sports", "nfl": "Sports", "nba": "Sports", "basketball": "Sports",
    "soccer": "Sports", "epl": "Sports", "premier league": "Sports", "uefa": "Sports",
    "fifa world cup": "Sports", "mlb": "Sports", "nhl": "Sports", "football": "Sports",
    "tennis": "Sports", "golf": "Sports", "olympics": "Sports",
    "fantasy football": "Sports", "hockey": "Sports", "super bowl": "Sports",
    "cfb": "Sports", "college football": "Sports", "nfl playoffs": "Sports",
    "big game": "Sports", "2026 fifa world cup": "Sports",

    # Econ/Finance
    "finance": "Econ/Finance", "economy": "Econ/Finance", "fed": "Econ/Finance",
    "interest rates": "Econ/Finance", "stocks": "Econ/Finance", "inflation": "Econ/Finance",
    "business": "Econ/Finance", "stock market": "Econ/Finance",
    "market predictions": "Econ/Finance", "investment": "Econ/Finance",
    "investments": "Econ/Finance", "markets": "Econ/Finance",

    # Culture
    "culture": "Culture", "movies": "Culture", "film": "Culture", "music": "Culture",
    "awards": "Culture", "oscars": "Culture", "tv": "Culture", "entertainment": "Culture",
    "taylor swift": "Culture",

    # Politics — broad, checked last among mapped tags
    "politics": "Politics", "elections": "Politics", "us election": "Politics",
    "usa election": "Politics", "2024 election": "Politics", "election": "Politics",
    "congress": "Politics", "senate": "Politics", "president": "Politics",
    "trump": "Politics", "2024 presidential election": "Politics",
    "republican party": "Politics", "vivek ramaswamy": "Politics",
    "ron desantis": "Politics", "kamala harris": "Politics",
    "joe biden": "Politics", "biden": "Politics", "nikki haley": "Politics",
    "chris christie": "Politics", "democratic party": "Politics",
    "us elections": "Politics", "gavin newsom": "Politics",
    "hillary clinton": "Politics", "robert f. kennedy jr.": "Politics",
    "democrats": "Politics", "global elections": "Politics",
    "republicans": "Politics", "presidential nomination": "Politics",
    "u.s. politics": "Politics", "kanye west": "Politics",
    "dean phillips": "Politics", "michelle obama": "Politics",
    "elizabeth warren": "Politics", "election results": "Politics",
    "presidential election": "Politics", "u.s. presidential election": "Politics",
    "presidential election 2024": "Politics", "tim scott": "Politics",
    "mike pence": "Politics", "margin of victory": "Politics",
    "popular vote": "Politics", "aoc": "Politics", "bernie sanders": "Politics",
    "iowa caucus": "Politics", "voting": "Politics", "primaries": "Politics",
    "trump presidency": "Politics",
    "doge": "Politics",  # Dept. of Govt Efficiency, NOT Dogecoin — verified via example title
}

# Structural / platform tags: explicitly excluded, never a category signal
# even if frequency is high. Distinct from "unmapped" (which falls to Other
# and gets counted).
EXCLUDED_TAGS: set[str] = {"all", "potusbanner"}

CATEGORY_PRECEDENCE: list[str] = [
    "Geopolitics", "Crypto", "Sports", "Econ/Finance", "Culture", "Politics",
]


def map_category(tags: list[str]) -> tuple[str, list[str]]:
    """Maps an event's raw tags to one of the 7 study categories.

    Case-folds every tag and drops EXCLUDED_TAGS before matching, then walks
    CATEGORY_PRECEDENCE and returns the first category for which any of the
    event's tags has a match in CATEGORY_MAP. No match at all -> "Other"
    (the caller is expected to count this share). Returns (category,
    tags_raw) with tags_raw unmodified for audit.
    """
    folded = [t.lower() for t in tags if t.lower() not in EXCLUDED_TAGS]
    mapped = {CATEGORY_MAP[t] for t in folded if t in CATEGORY_MAP}
    for category in CATEGORY_PRECEDENCE:
        if category in mapped:
            return category, tags
    return "Other", tags
