"""
Mod validatie voor tournament pool slots.

Regels:
- Alle pool scores: verplicht SM (ScoreV2) + NF
- NM slots:   exact SM + NF              (geen extra mods)
- HD slots:   exact SM + NF + HD
- HR slots:   exact SM + NF + HR
- DT slots:   exact SM + NF + DT         (NC telt ook als DT)
- FM/TB slots: vrij, maar EZ/HT altijd verboden, SM + NF verplicht
- EX slots:   vrij, alles toegestaan

Opmerking: de osu! API geeft ScoreV2 terug als "SM" (niet "SV2").
Slot naam bepaalt de categorie: NM1, HD2, HR1, DT3, FM1, TB1, EX1 etc.
"""

ALWAYS_FORBIDDEN = {"EZ", "HT"}
BASE_REQUIRED = {"SM", "NF"}  # SM = ScoreV2 in osu! API

SLOT_EXTRA = {
    "NM": set(),
    "HD": {"HD"},
    "HR": {"HR"},
    "DT": {"DT"},
    "FM": None,   # vrij
    "TB": None,   # vrij
    "EX": None,   # alles toegestaan
}

IGNORED_MODS = {"SD", "PF", "MR"}


def normalize_mods(mods: str) -> set:
    """
    Zet mod string om naar een set.
    Normaliseert NC -> DT, negeert presentatie-mods.
    SM = ScoreV2 (osu! API acronym).
    """
    if not mods or mods == "NM":
        return set()
    known = {
        "SM", "SV2", "CL",  # ScoreV2 varianten
        "NF", "HD", "HR", "DT", "NC", "EZ", "HT",
        "FL", "SD", "PF", "RX", "AP", "SO", "MR", "BL"
    }
    result = set()
    i = 0
    s = mods.upper()
    while i < len(s):
        # Probeer eerst 3-letter codes (geen bekende, maar voor safety)
        chunk2 = s[i:i+2]
        if chunk2 in known:
            if chunk2 == "NC":
                result.add("DT")
            elif chunk2 == "SV2":
                result.add("SM")  # normaliseer SV2 naar SM
            elif chunk2 not in IGNORED_MODS:
                result.add(chunk2)
            i += 2
        else:
            i += 1
    return result


def get_slot_category(slot: str) -> str:
    return "".join(c for c in slot.upper() if c.isalpha())


def validate_mods(mods: str, slot: str) -> tuple[bool, str | None]:
    mod_set = normalize_mods(mods)
    category = get_slot_category(slot)

    if category == "EX":
        return True, None

    forbidden = mod_set & ALWAYS_FORBIDDEN
    if forbidden:
        return False, f"Verboden mods voor {slot}: {', '.join(sorted(forbidden))}"

    if category in ("FM", "TB"):
        if "SM" not in mod_set:
            return False, f"ScoreV2 (SM) verplicht voor {slot}"
        if "NF" not in mod_set:
            return False, f"NoFail (NF) verplicht voor {slot}"
        return True, None

    missing_base = BASE_REQUIRED - mod_set
    if missing_base:
        labels = {"SM": "ScoreV2", "NF": "NoFail"}
        missing_names = [labels.get(m, m) for m in sorted(missing_base)]
        return False, f"Verplichte mods ontbreken voor {slot}: {', '.join(missing_names)}"

    extra = SLOT_EXTRA.get(category)
    if extra is None:
        return True, None

    expected = BASE_REQUIRED | extra

    if mod_set != expected:
        unexpected = mod_set - expected
        missing = expected - mod_set
        parts = []
        if unexpected:
            parts.append(f"niet toegestaan: {', '.join(sorted(unexpected))}")
        if missing:
            labels = {"SM": "ScoreV2", "NF": "NoFail"}
            missing_names = [labels.get(m, m) for m in sorted(missing)]
            parts.append(f"ontbreekt: {', '.join(missing_names)}")
        return False, f"Verkeerde mods voor {slot} — " + " | ".join(parts)

    return True, None


def describe_required_mods(slot: str) -> str:
    category = get_slot_category(slot)
    if category == "EX":
        return "Vrij (alles toegestaan)"
    if category in ("FM", "TB"):
        return "SM + NF + vrije mods (geen EZ/HT)"
    extra = SLOT_EXTRA.get(category, set()) or set()
    required = BASE_REQUIRED | extra
    labels = {"SM": "ScoreV2", "NF": "NoFail", "HD": "Hidden", "HR": "HardRock", "DT": "DoubleTime"}
    return " + ".join(labels.get(m, m) for m in sorted(required))
