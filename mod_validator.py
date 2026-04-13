"""
Mod validatie voor tournament pool slots.

Regels:
- Alle slots: verplicht CL (classic/v2) + NF
- NM slots:   exact CL + NF  (geen extra mods)
- HD slots:   exact CL + NF + HD
- HR slots:   exact CL + NF + HR
- DT slots:   exact CL + NF + DT  (NC telt ook als DT)
- FM/EX slots: vrij, maar EZ/HT altijd verboden tenzij EX
- EZ/HT:      altijd verboden behalve EX slots

Slot naam bepaalt de categorie: NM1, HD2, HR1, DT3, FM1, TB1, EX1 etc.
"""

# Mods die altijd verboden zijn (behalve in EX)
ALWAYS_FORBIDDEN = {"EZ", "HT"}

# Verplichte basis mods voor alle slots
BASE_REQUIRED = {"CL", "NF"}

# Verplichte extra mods per slot categorie (bovenop BASE_REQUIRED)
SLOT_EXTRA = {
    "NM": set(),
    "HD": {"HD"},
    "HR": {"HR"},
    "DT": {"DT"},
    "NC": {"DT"},   # NC wordt intern als DT behandeld
    "FM": None,     # Vrij
    "TB": None,     # Vrij (tiebreaker)
    "EX": None,     # Vrij, EZ/HT ook toegestaan
}

# NC → DT normalisatie
def normalize_mods(mods: str) -> set:
    """Zet mod string om naar een set. Normaliseert NC naar DT."""
    if not mods or mods == "NM":
        return set()
    # Splits op bekende 2-letter mod codes
    known = {"CL", "NF", "HD", "HR", "DT", "NC", "EZ", "HT", "FL", "SD", "PF", "RX", "AP", "SO", "MR"}
    result = set()
    i = 0
    while i < len(mods):
        chunk = mods[i:i+2].upper()
        if chunk in known:
            # Normaliseer NC naar DT
            result.add("DT" if chunk == "NC" else chunk)
            i += 2
        else:
            i += 1
    return result


def get_slot_category(slot: str) -> str:
    """Haal de categorie op uit een slot naam. NM1 -> NM, HD2 -> HD, etc."""
    return "".join(c for c in slot.upper() if c.isalpha())


def validate_mods(mods: str, slot: str) -> tuple[bool, str | None]:
    """
    Valideer of de mods correct zijn voor een gegeven slot.

    Returns:
        (True, None) als de mods geldig zijn
        (False, reden) als de mods ongeldig zijn
    """
    mod_set = normalize_mods(mods)
    category = get_slot_category(slot)

    # EX slot: alles toegestaan
    if category == "EX":
        return True, None

    # EZ/HT altijd verboden buiten EX
    forbidden = mod_set & ALWAYS_FORBIDDEN
    if forbidden:
        return False, f"Verboden mods voor {slot}: {', '.join(sorted(forbidden))}"

    # FM/TB: vrij, alleen EZ/HT check hierboven
    if category in ("FM", "TB"):
        return True, None

    # Alle andere slots: check BASE_REQUIRED aanwezig
    missing_base = BASE_REQUIRED - mod_set
    if missing_base:
        return False, f"Verplichte mods ontbreken voor {slot}: {', '.join(sorted(missing_base))}"

    # Bepaal de verwachte exacte mod set
    extra = SLOT_EXTRA.get(category)
    if extra is None:
        # Onbekend slot type, behandel als FM
        return True, None

    expected = BASE_REQUIRED | extra

    # Strict exact: geen mods meer en geen minder dan expected
    if mod_set != expected:
        unexpected = mod_set - expected
        missing = expected - mod_set
        parts = []
        if unexpected:
            parts.append(f"niet toegestaan: {', '.join(sorted(unexpected))}")
        if missing:
            parts.append(f"ontbreekt: {', '.join(sorted(missing))}")
        return False, f"Verkeerde mods voor {slot} — " + " | ".join(parts)

    return True, None


def describe_required_mods(slot: str) -> str:
    """Geeft een leesbare beschrijving van de vereiste mods voor een slot."""
    category = get_slot_category(slot)
    if category == "EX":
        return "Vrij (alles toegestaan)"
    if category in ("FM", "TB"):
        return "Vrij (geen EZ/HT)"
    extra = SLOT_EXTRA.get(category, set()) or set()
    required = BASE_REQUIRED | extra
    return " + ".join(sorted(required)) or "NM"
