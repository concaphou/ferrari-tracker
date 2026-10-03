"""
Everything you are likely to tweak lives in this file.
"""

# ---------------------------------------------------------------------------
# Schedule (America/Chicago handles CST/CDT automatically)
# ---------------------------------------------------------------------------
TIMEZONE = "America/Chicago"
# A run counts for a slot if it starts inside the window [start_hour, end_hour).
# The windows are wider than one hour because GitHub's scheduler can start late.
SLOTS = {
    "AM": (11, 14),   # 11:00 am run
    "PM": (17, 20),   # 5:00 pm run
}

# ---------------------------------------------------------------------------
# Model groups. GTS / Spider / Spyder are combined into one group per family.
# 488 Pista, Pista Spider and Challenge cars are excluded.
# ---------------------------------------------------------------------------
MODEL_GROUPS = [
    "488 GTB",
    "488 GTS/Spider",
    "296 GTB",
    "296 GTS/Spider",
    "F8 Coupe (Tributo)",
    "F8 Spider",
]

EXCLUDE_MODEL_WORDS = ["pista", "challenge", "competizione", "assetto"]

# ---------------------------------------------------------------------------
# Red / near-red colors to drop (matched as whole words, case-insensitive,
# against BOTH exterior and interior color). Covers Ferrari's named reds:
# Rosso Corsa, Scuderia, Fuoco, Mugello, Dino, Barchetta, Fiorano, Imola,
# Maranello, Monza, Portofino, Magma, Trionfale, Formula 1, Spettacolo,
# Rosso Ferrari (interior leather), Bordeaux, etc.
# ---------------------------------------------------------------------------
RED_WORDS = [
    "red", "reds", "rosso", "rossa", "rosa corsa", "bordeaux", "burgundy",
    "maroon", "crimson", "scarlet", "scarlatto", "ruby", "rubino", "cherry",
    "wine", "claret", "cardinal", "carmine", "carminio", "vermilion",
    "garnet", "oxblood", "amaranto", "fuoco", "magma", "mugello",
]

# ---------------------------------------------------------------------------
# Search pages. Each site is searched nationwide; results are filtered
# locally, so a search that returns extra cars is fine.
# ---------------------------------------------------------------------------
HOME_ZIP = "60601"  # only used as the search origin; radius is nationwide

# Cars.com: one search with all six models. If a model slug is wrong,
# Cars.com just ignores it; see README for how to fix.
CARSCOM_MODEL_SLUGS = [
    "ferrari-488_gtb", "ferrari-488_spider", "ferrari-488_gts",
    "ferrari-296_gtb", "ferrari-296_gts",
    "ferrari-f8_tributo", "ferrari-f8_spider",
]

# CarGurus: nationwide search per model, using CarGurus's model IDs.
# The 488 and F8 IDs cover both coupe and Spider; each car is sorted locally.
# A None ID is discovered automatically from links on the other model pages.
CARGURUS_MODEL_IDS = {
    "488": "d2333",
    "296-GTB": "d3240",
    "296-GTS": None,
    "F8": "d3048",
}
CARGURUS_EXTRA_URLS = []  # paste any extra CarGurus search URLs here

# Ferrari Approved (preowned.ferrari.com): one US-wide page per model.
FERRARI_MODEL_SLUGS = ["488-gtb", "488-spider", "296-gtb", "296-gts",
                       "f8-tributo", "f8-spider"]
FERRARI_EXTRA_URLS = []

MAX_PHOTOS = 6  # photos kept per car

# ---------------------------------------------------------------------------
# Politeness / limits
# ---------------------------------------------------------------------------
MAX_PAGES_PER_SEARCH = 15        # pagination cap per search URL
PAGE_DELAY_SECONDS = (3, 7)      # random pause between page loads
MAX_DETAIL_PAGES_PER_RUN = 120   # detail pages visited to fill missing colors/city
DETAIL_DELAY_SECONDS = (2, 5)

# City ranking settings
CITY_WINDOW_DAYS = 90            # history window used for the city ranking
CITY_MIN_CARS = 3                # cities with fewer unique cars are hidden
