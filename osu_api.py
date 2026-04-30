import aiohttp
import os
import json
from datetime import datetime, timezone

# Mod categorieën die per pool slot vereist zijn
MOD_CATEGORY_MODS = {
    "NM": [],           # alleen NF verplicht
    "HD": ["HD"],
    "HR": ["HR"],
    "DT": ["DT"],
    "FL": ["FL"],
    "EZ": ["EZ"],
}

# Mods die altijd geblokkeerd zijn (behalve NF zelf)
BANNED_MODS = {"RX", "AP", "AT", "CN", "SO"}


def get_mod_acronyms(mods_raw) -> list[str]:
    """Haal mod acronyms op uit het API response (werkt voor stable én lazer)."""
    if isinstance(mods_raw, list):
        result = []
        for m in mods_raw:
            if isinstance(m, str):
                result.append(m.upper())
            elif isinstance(m, dict):
                acronym = m.get("acronym", "")
                if acronym:
                    result.append(acronym.upper())
        return result
    elif isinstance(mods_raw, str):
        # Legacy: komma-gescheiden string of geconcateneerde string
        if "," in mods_raw:
            return [m.strip().upper() for m in mods_raw.split(",") if m.strip()]
        return [mods_raw.upper()] if mods_raw and mods_raw != "NM" else []
    return []


def validate_pool_score(mods_list: list[str], mod_category: str) -> tuple[bool, str]:
    """
    Controleer of een score geldig is voor een pool slot.
    Regels:
    - NF moet altijd aanwezig zijn
    - De vereiste mod van het slot moet aanwezig zijn
    - Verboden mods mogen niet aanwezig zijn
    - Overige mods zijn toegestaan (bijv. NF+HD+NM1 is ongeldig, maar dat detecteer je via slot)
    
    Returns: (is_valid, reden_als_ongeldig)
    """
    mods_set = set(mods_list)

    if "NF" not in mods_set:
        return False, "NF ontbreekt"

    banned = mods_set & BANNED_MODS
    if banned:
        return False, f"Verboden mod(s): {', '.join(banned)}"

    required = MOD_CATEGORY_MODS.get(mod_category.upper(), [])
    for req in required:
        if req not in mods_set:
            return False, f"{req} ontbreekt voor slot {mod_category}"

    return True, ""


class OsuAPI:
    BASE = "https://osu.ppy.sh/api/v2"
    TOKEN_URL = "https://osu.ppy.sh/oauth/token"

    def __init__(self):
        self.client_id = os.getenv("OSU_CLIENT_ID")
        self.client_secret = os.getenv("OSU_CLIENT_SECRET")
        self.token = None
        self.session: aiohttp.ClientSession = None

    async def ensure_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def get_token(self):
        await self.ensure_session()
        resp = await self.session.post(self.TOKEN_URL, json={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
            "scope": "public"
        })
        data = await resp.json()
        self.token = data.get("access_token")

    async def request(self, endpoint, params=None):
        await self.ensure_session()
        if not self.token:
            await self.get_token()

        headers = {"Authorization": f"Bearer {self.token}"}
        resp = await self.session.get(
            f"{self.BASE}{endpoint}", headers=headers, params=params
        )

        if resp.status == 401:
            await self.get_token()
            headers["Authorization"] = f"Bearer {self.token}"
            resp = await self.session.get(
                f"{self.BASE}{endpoint}", headers=headers, params=params
            )

        if resp.status != 200:
            return None
        return await resp.json()

    async def get_user(self, username: str):
        return await self.request(f"/users/{username}/osu", params={"key": "username"})

    async def get_user_by_id(self, osu_id: int):
        return await self.request(f"/users/{osu_id}/osu")

    async def get_recent_scores(self, osu_id: int, limit=50):
        """Haalt recente scores op — bevat zowel stable als lazer scores."""
        return await self.request(f"/users/{osu_id}/scores/recent", params={
            "limit": limit,
            "include_fails": 1,
            "legacy_only": 0,   # 0 = ook lazer scores
        })

    async def get_beatmap(self, beatmap_id: int):
        return await self.request(f"/beatmaps/{beatmap_id}")

    def is_lazer_score(self, raw: dict) -> bool:
        """
        Lazer scores hebben build_id of classic_total_hit_count of
        de mods als lijst van dicts met settings.
        """
        # Duidelijkste indicator: build_id aanwezig in score data
        if raw.get("build_id"):
            return True
        # Of: mods is een lijst van dicts met een 'settings' key
        mods = raw.get("mods", [])
        if isinstance(mods, list):
            for m in mods:
                if isinstance(m, dict) and "settings" in m:
                    return True
        # Fallback: legacy_score_id ontbreekt maar score_id bestaat (lazer patroon)
        if raw.get("legacy_score_id") is None and raw.get("id"):
            return True
        return False

    def parse_score(self, raw: dict, osu_id: int, discord_id=None, pool_map=None) -> dict:
        """
        Verwerk een raw API score naar een database dict.
        pool_map: dict met pool_id, pool_slot, mod_category (als de map in een pool zit)
        """
        stats = raw.get("statistics", {})
        mods_raw = raw.get("mods", [])
        mods_list = get_mod_acronyms(mods_raw)
        mod_str = "".join(mods_list) if mods_list else "NM"
        has_nf = "NF" in mods_list

        client_type = "lazer" if self.is_lazer_score(raw) else "stable"

        submitted_str = raw.get("ended_at") or raw.get("created_at", "")
        try:
            submitted_at = datetime.fromisoformat(submitted_str.replace("Z", "+00:00"))
            if submitted_at.tzinfo is None:
                submitted_at = submitted_at.replace(tzinfo=timezone.utc)
        except Exception:
            submitted_at = datetime.now(timezone.utc)

        beatmap = raw.get("beatmap", {})
        beatmap_id = beatmap.get("id") or raw.get("beatmap_id", 0)

        # Pool validatie
        is_pool_score = pool_map is not None
        pool_id = None
        pool_slot = None
        is_valid = True
        invalid_reason = None

        if pool_map:
            pool_id = pool_map["pool_id"]
            pool_slot = pool_map["pool_slot"]
            mod_category = pool_map["mod_category"]
            is_valid, invalid_reason = validate_pool_score(mods_list, mod_category)
            if not raw.get("passed", True):
                is_valid = False
                invalid_reason = "Score niet gepasst"

        return {
            "osu_score_id":   raw.get("id"),
            "osu_id":         osu_id,
            "discord_id":     discord_id,
            "beatmap_id":     beatmap_id,
            "score":          raw.get("total_score") or raw.get("score", 0),
            "accuracy":       round((raw.get("accuracy") or 0) * 100, 2),
            "max_combo":      raw.get("max_combo", 0),
            "mods":           mod_str,
            "mods_list":      json.dumps(mods_list),
            "rank":           raw.get("rank", "F"),
            "count_300":      stats.get("great") or stats.get("count_300", 0),
            "count_100":      stats.get("ok") or stats.get("count_100", 0),
            "count_50":       stats.get("meh") or stats.get("count_50", 0),
            "count_miss":     stats.get("miss") or stats.get("count_miss", 0),
            "pp":             raw.get("pp") or 0,
            "is_pass":        raw.get("passed", True),
            "client_type":    client_type,
            "has_nf":         has_nf,
            "is_pool_score":  is_pool_score,
            "pool_id":        pool_id,
            "pool_slot":      pool_slot,
            "is_valid":       is_valid,
            "invalid_reason": invalid_reason,
            "submitted_at":   submitted_at,
        }

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
