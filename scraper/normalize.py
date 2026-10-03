"""Model classification, color filtering and field clean-up."""

import re
from . import config

_RED_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in config.RED_WORDS) + r")\b", re.I
)
_EXCLUDE_RE = re.compile(
    r"\b(" + "|".join(config.EXCLUDE_MODEL_WORDS) + r")\b", re.I
)
_STATE_RE = re.compile(r"^\s*([A-Za-z .'\-]+?),\s*([A-Z]{2})\b")


def classify_model(*texts):
    """Map free text (title/model/trim) to one of config.MODEL_GROUPS, or None."""
    t = " ".join(str(x) for x in texts if x).lower()
    if not t or _EXCLUDE_RE.search(t):
        return None
    t = t.replace("-", " ")
    open_top = bool(re.search(r"\b(gts|spider|spyder|convertible|cabriolet)\b", t))

    if re.search(r"\b488\b", t):
        if open_top:
            return "488 GTS/Spider"
        if re.search(r"\bgtb\b", t):
            return "488 GTB"
        return None
    if re.search(r"\b296\b", t):
        if open_top:
            return "296 GTS/Spider"
        if re.search(r"\bgtb\b", t):
            return "296 GTB"
        return None
    if re.search(r"\bf8\b", t) or "f8tributo" in t.replace(" ", ""):
        return "F8 Spider" if open_top else "F8 Coupe (Tributo)"
    return None


def is_red(color):
    return bool(color) and bool(_RED_RE.search(str(color)))


def clean_color(c):
    c = re.sub(r"\s+", " ", str(c or "")).strip(" :-,")
    if c.lower() in ("", "unknown", "n/a", "na", "other", "not specified", "-"):
        return ""
    return c[:60]


def split_city_state(city, state="", location=""):
    """Return (City, ST) from whatever the site gave us."""
    city, state = str(city or "").strip(), str(state or "").strip()
    if city and not state:
        m = _STATE_RE.match(city)
        if m:
            return m.group(1).strip().title(), m.group(2)
    if not city and location:
        m = _STATE_RE.match(str(location))
        if m:
            return m.group(1).strip().title(), m.group(2)
    return city.title(), state.upper()[:2]


def to_int(v):
    s = re.sub(r"[^\d]", "", str(v or "").split(".")[0])
    return int(s) if s else None
