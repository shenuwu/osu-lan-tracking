import aiohttp
import os
from datetime import datetime, timezone


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
        resp = await self.session.get(f"{self.BASE}{endpoint}", headers=headers, params=params)
        if resp.status == 401:
            await self.get_token()
            headers["Authorization"] = f"Bearer {self.token}"
            resp = await self.session.get(f"{self.BASE}{endpoint}", headers=headers, params=params)
        if resp.status != 200:
            return None
        return await resp.json()

    async def get_user(self, username: str):
        return await self.request(f"/users/{username}/osu", params={"key": "username"})

    async def get_user_by_id(self, osu_id: int):
        return await self.request(f"/users/{osu_id}/osu")

    async def get_recent_scores(self, osu_id: int, limit=50):
        return await self.request(f"/users/{osu_id}/scores/recent", params={
            "limit": limit,
            "include_fails": 1,
            "legacy_only": 0
        })

    async def get_beatmap(self, beatmap_id: int):
        return await self.request(f"/beatmaps/{beatmap_id}")

    def parse_score(self, raw: dict, osu_id: int, discord_id=None) -> dict:
        stats = raw.get("statistics", {})
        mods = raw.get("mods", [])
        if isinstance(mods, list):
            mod_str = "".join(m if isinstance(m, str) else m.get("acronym", "") for m in mods) or "NM"
        else:
            mod_str = str(mods) or "NM"

        submitted_str = raw.get("ended_at") or raw.get("created_at", "")
        try:
            submitted_at = datetime.fromisoformat(submitted_str.replace("Z", "+00:00"))
            if submitted_at.tzinfo is None:
                submitted_at = submitted_at.replace(tzinfo=timezone.utc)
        except Exception:
            submitted_at = datetime.now(timezone.utc)

        beatmap = raw.get("beatmap", {})
        beatmap_id = beatmap.get("id") or raw.get("beatmap_id", 0)

        return {
            "osu_score_id": raw.get("id"),
            "osu_id": osu_id,
            "discord_id": discord_id,
            "beatmap_id": beatmap_id,
            "score": raw.get("total_score") or raw.get("score", 0),
            "accuracy": round((raw.get("accuracy") or 0) * 100, 2),
            "max_combo": raw.get("max_combo", 0),
            "mods": mod_str,
            "rank": raw.get("rank", "F"),
            "count_300": stats.get("great") or stats.get("count_300", 0),
            "count_100": stats.get("ok") or stats.get("count_100", 0),
            "count_50": stats.get("meh") or stats.get("count_50", 0),
            "count_miss": stats.get("miss") or stats.get("count_miss", 0),
            "pp": raw.get("pp") or 0,
            "is_pass": raw.get("passed", True),
            "submitted_at": submitted_at,
        }

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
