from src.panel.categories import map_category


def test_map_category_geopolitics_precedence_over_politics():
    category, tags_raw = map_category(["Politics", "Israel"])
    assert category == "Geopolitics"
    assert tags_raw == ["Politics", "Israel"]


def test_map_category_crypto_precedence_over_econ_finance():
    category, _ = map_category(["Finance", "Bitcoin"])
    assert category == "Crypto"


def test_map_category_sports_precedence_over_culture():
    category, _ = map_category(["Culture", "NBA"])
    assert category == "Sports"


def test_map_category_doge_maps_to_politics_not_crypto():
    category, _ = map_category(["DOGE"])
    assert category == "Politics"


def test_map_category_excluded_tag_ignored():
    category, _ = map_category(["All", "Politics"])
    assert category == "Politics"


def test_map_category_all_excluded_and_unmapped_falls_to_other():
    category, _ = map_category(["All", "Tech", "internet"])
    assert category == "Other"


def test_map_category_unmapped_falls_to_other():
    category, _ = map_category(["Science", "Pandemics"])
    assert category == "Other"


def test_map_category_case_insensitive():
    category, _ = map_category(["POLITICS"])
    assert category == "Politics"


def test_map_category_empty_tags_is_other():
    category, tags_raw = map_category([])
    assert category == "Other"
    assert tags_raw == []


def test_map_category_returns_original_tags_raw_unmodified():
    category, tags_raw = map_category(["Sports", "nba"])
    assert tags_raw == ["Sports", "nba"]  # not folded/deduped in the returned tuple
