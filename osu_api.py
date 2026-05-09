import aiohttp
import os
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("osu_api")

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

    async def get_recent_scores(self, osu_id: int, limit=50, legacy_only: bool = False):
        """Haalt recente scores op. legacy_only=False voor lazer, True voor stable."""
        return await self.request(f"/users/{osu_id}/scores/recent", params={
            "limit": limit,
            "include_fails": 1,
            "legacy_only": 1 if legacy_only else 0,
        })

    async def get_score_with_token(self, score_id: int, access_token: str) -> dict | None:
        """Haal score op met user OAuth token — geeft total_score terug."""
        await self.ensure_session()
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = await self.session.get(f"{self.BASE}/scores/{score_id}", headers=headers)
        if resp.status == 200:
            return await resp.json()
        return None

    async def refresh_user_token(self, refresh_token: str) -> dict | None:
        """Vernieuw een verlopen OAuth token."""
        await self.ensure_session()
        client_id     = os.getenv("OSU_OAUTH_CLIENT_ID") or self.client_id
        client_secret = os.getenv("OSU_OAUTH_CLIENT_SECRET") or self.client_secret
        resp = await self.session.post(self.TOKEN_URL, json={
            "client_id":     client_id,
            "client_secret": client_secret,
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "scope":         "public identify",
        })
        if resp.status == 200:
            return await resp.json()
        return None

    async def get_user_me(self, access_token: str) -> dict | None:
        """Haal osu! user op via OAuth token."""
        await self.ensure_session()
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = await self.session.get(f"{self.BASE}/me/osu", headers=headers)
        if resp.status == 200:
            return await resp.json()
        return None

    async def get_beatmap(self, beatmap_id: int):
        return await self.request(f"/beatmaps/{beatmap_id}")

    def is_lazer_score(self, raw: dict) -> bool:
        """
        Met legacy_only=1 geeft de API het 'type' veld terug:
        'solo_score' = lazer, 'score' of ontbrekend = stable.
        """
        score_type = raw.get("type", "")
        if score_type == "solo_score":
            return True
        # Fallback: legacy_score_id is None voor lazer scores
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

        # Score ophalen — lazer recent endpoint geeft score=0, bereken zelf
        client_type = "lazer" if self.is_lazer_score(raw) else "stable"
        raw_score = raw.get("score") or raw.get("total_score") or 0

        if client_type == "lazer" and raw_score == 0:
            # Probeer max_combo te halen: eerst uit pool_map (opgeslagen bij add_map),
            # dan uit de beatmap data in de raw response
            bm_max_combo = 0
            if pool_map and pool_map.get("max_combo"):
                bm_max_combo = pool_map["max_combo"]
            elif beatmap.get("max_combo"):
                bm_max_combo = beatmap["max_combo"]

            player_combo = raw.get("max_combo") or 0

            if bm_max_combo > 0 and player_combo > 0:
                raw_score = self._calculate_scorev2(
                    accuracy=raw.get("accuracy") or 0,
                    max_combo=player_combo,
                    beatmap_max_combo=bm_max_combo,
                )
            else:
                # Geen combo data — bereken alleen op basis van accuracy (combo portion = 0)
                acc = raw.get("accuracy") or 0
                raw_score = int(1_000_000 * acc * 0.3)
                logger.warning(f"Geen beatmap max_combo beschikbaar voor beatmap {beatmap_id}, score berekend op accuracy only")
        # Geen NF x2 nodig — formule berekent al de score op 1M scale (zonder NF penalty)

        return {
            "osu_score_id":   raw.get("id"),
            "osu_id":         osu_id,
            "discord_id":     discord_id,
            "beatmap_id":     beatmap_id,
            "score":          raw_score,
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
            "map_length":     (pool_map.get("total_length") or 0) if pool_map else (beatmap.get("total_length") or 0),
        }

    def _calculate_scorev2(self, accuracy: float, max_combo: int, beatmap_max_combo: int) -> int:
        """
        osu! lazer standaard score formule:
        total_score = 1_000_000 * (accuracy * 0.3 + (combo / max_combo) * 0.7)
        Max is 1_000_000 bij SS FC. NF halveert dit in-game, wij berekenen de echte waarde.
        """
        if beatmap_max_combo > 0:
            combo_ratio = min(max_combo / beatmap_max_combo, 1.0)
        else:
            combo_ratio = 1.0

        return int(1_000_000 * (accuracy * 0.3 + combo_ratio * 0.7))

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
