import asyncpg
import os
from datetime import datetime, timezone


class Database:
    def __init__(self):
        self.pool = None

    async def init(self):
        self.pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))
        await self.create_tables()
        print("Database verbonden en tabellen aangemaakt")

    async def create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    discord_id BIGINT PRIMARY KEY,
                    osu_username TEXT NOT NULL,
                    osu_id BIGINT NOT NULL UNIQUE,
                    added_by BIGINT,
                    added_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS pools (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    channel_id BIGINT UNIQUE,
                    guild_id BIGINT NOT NULL,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    active BOOLEAN DEFAULT TRUE
                );

                CREATE TABLE IF NOT EXISTS pool_maps (
                    id SERIAL PRIMARY KEY,
                    pool_id INT REFERENCES pools(id) ON DELETE CASCADE,
                    beatmap_id BIGINT NOT NULL,
                    beatmapset_id BIGINT,
                    title TEXT,
                    artist TEXT,
                    version TEXT,
                    slot TEXT,
                    UNIQUE(pool_id, beatmap_id)
                );

                CREATE TABLE IF NOT EXISTS scores (
                    id SERIAL PRIMARY KEY,
                    osu_score_id BIGINT UNIQUE NOT NULL,
                    osu_id BIGINT NOT NULL,
                    discord_id BIGINT REFERENCES players(discord_id),
                    beatmap_id BIGINT NOT NULL,
                    score BIGINT NOT NULL,
                    accuracy FLOAT NOT NULL,
                    max_combo INT NOT NULL,
                    mods TEXT DEFAULT 'NM',
                    rank TEXT,
                    count_300 INT DEFAULT 0,
                    count_100 INT DEFAULT 0,
                    count_50 INT DEFAULT 0,
                    count_miss INT DEFAULT 0,
                    pp FLOAT DEFAULT 0,
                    is_pass BOOLEAN DEFAULT TRUE,
                    is_valid BOOLEAN DEFAULT TRUE,
                    invalid_reason TEXT,
                    submitted_at TIMESTAMPTZ NOT NULL,
                    tracked_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS tracking_sessions (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    started_by BIGINT,
                    start_time TIMESTAMPTZ NOT NULL,
                    end_time TIMESTAMPTZ,
                    interval_seconds INT DEFAULT 60,
                    is_test BOOLEAN DEFAULT FALSE,
                    active BOOLEAN DEFAULT TRUE
                );

                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    log_channel_id BIGINT,
                    score_channel_id BIGINT,
                    tracking_active BOOLEAN DEFAULT FALSE,
                    tracking_session_id INT REFERENCES tracking_sessions(id)
                );
            """)
            # Migraties voor bestaande databases
            await conn.execute("ALTER TABLE scores ADD COLUMN IF NOT EXISTS is_valid BOOLEAN DEFAULT TRUE")
            await conn.execute("ALTER TABLE scores ADD COLUMN IF NOT EXISTS invalid_reason TEXT")

    # --- Players ---
    async def add_player(self, discord_id, osu_username, osu_id, added_by=None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO players (discord_id, osu_username, osu_id, added_by)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (discord_id) DO UPDATE
                SET osu_username=$2, osu_id=$3
            """, discord_id, osu_username, osu_id, added_by)

    async def remove_player(self, discord_id):
        async with self.pool.acquire() as conn:
            return await conn.execute("DELETE FROM players WHERE discord_id=$1", discord_id)

    async def get_player(self, discord_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM players WHERE discord_id=$1", discord_id)

    async def get_player_by_osu_id(self, osu_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM players WHERE osu_id=$1", osu_id)

    async def get_all_players(self):
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM players ORDER BY osu_username")

    # --- Pools ---
    async def create_pool(self, name, channel_id, guild_id, created_by):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                INSERT INTO pools (name, channel_id, guild_id, created_by)
                VALUES ($1, $2, $3, $4)
                RETURNING *
            """, name, channel_id, guild_id, created_by)

    async def get_pool_by_channel(self, channel_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM pools WHERE channel_id=$1", channel_id)

    async def get_all_pools(self, guild_id):
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM pools WHERE guild_id=$1 ORDER BY created_at", guild_id)

    async def add_map_to_pool(self, pool_id, beatmap_id, beatmapset_id, title, artist, version, slot):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO pool_maps (pool_id, beatmap_id, beatmapset_id, title, artist, version, slot)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (pool_id, beatmap_id) DO NOTHING
            """, pool_id, beatmap_id, beatmapset_id, title, artist, version, slot)

    async def get_pool_maps(self, pool_id):
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM pool_maps WHERE pool_id=$1 ORDER BY slot", pool_id)

    async def remove_map_from_pool(self, pool_id, beatmap_id):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM pool_maps WHERE pool_id=$1 AND beatmap_id=$2", pool_id, beatmap_id)

    # --- Scores ---
    async def save_score(self, score_data: dict):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO scores (osu_score_id, osu_id, discord_id, beatmap_id, score,
                    accuracy, max_combo, mods, rank, count_300, count_100, count_50,
                    count_miss, pp, is_pass, is_valid, invalid_reason, submitted_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                ON CONFLICT (osu_score_id) DO NOTHING
            """,
            score_data["osu_score_id"], score_data["osu_id"], score_data.get("discord_id"),
            score_data["beatmap_id"], score_data["score"], score_data["accuracy"],
            score_data["max_combo"], score_data["mods"], score_data["rank"],
            score_data["count_300"], score_data["count_100"], score_data["count_50"],
            score_data["count_miss"], score_data.get("pp", 0), score_data["is_pass"],
            score_data.get("is_valid", True), score_data.get("invalid_reason"),
            score_data["submitted_at"])

    async def score_exists(self, osu_score_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id FROM scores WHERE osu_score_id=$1", osu_score_id)
            return row is not None

    async def get_player_stats(self, discord_id, since: datetime = None):
        async with self.pool.acquire() as conn:
            base = "SELECT * FROM scores WHERE discord_id=$1"
            args = [discord_id]
            if since:
                base += " AND submitted_at >= $2"
                args.append(since)
            return await conn.fetch(base + " ORDER BY submitted_at DESC", *args)

    async def get_pool_leaderboard(self, pool_id):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT s.*, p.osu_username, pm.title, pm.artist, pm.version, pm.slot
                FROM scores s
                JOIN players p ON p.discord_id = s.discord_id
                JOIN pool_maps pm ON pm.beatmap_id = s.beatmap_id
                WHERE pm.pool_id = $1 AND s.is_pass = TRUE AND s.is_valid = TRUE
                ORDER BY pm.slot, s.score DESC
            """, pool_id)

    async def get_global_leaderboard(self, guild_id, since: datetime = None):
        async with self.pool.acquire() as conn:
            query = """
                SELECT p.osu_username, p.discord_id,
                    COUNT(s.id) as maps_played,
                    AVG(s.accuracy) as avg_accuracy,
                    MAX(s.score) as top_score,
                    SUM(s.score) as total_score,
                    AVG(s.max_combo) as avg_combo,
                    SUM(CASE WHEN s.rank IN ('S','SS','X','XH') THEN 1 ELSE 0 END) as s_ranks,
                    COUNT(CASE WHEN s.count_miss = 0 AND s.is_pass THEN 1 END) as fc_count
                FROM players p
                LEFT JOIN scores s ON s.discord_id = p.discord_id
                WHERE p.discord_id IN (
                    SELECT discord_id FROM players WHERE discord_id IN (
                        SELECT discord_id FROM scores WHERE discord_id IS NOT NULL
                    )
                )
                GROUP BY p.osu_username, p.discord_id
                ORDER BY total_score DESC NULLS LAST
            """
            return await conn.fetch(query)

    # --- Guild settings ---
    async def get_guild_settings(self, guild_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM guild_settings WHERE guild_id=$1", guild_id)
            if not row:
                await conn.execute("INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
                row = await conn.fetchrow("SELECT * FROM guild_settings WHERE guild_id=$1", guild_id)
            return row

    async def update_guild_settings(self, guild_id, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k}=${i+2}" for i, k in enumerate(kwargs))
        vals = list(kwargs.values())
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE guild_settings SET {sets} WHERE guild_id=$1",
                guild_id, *vals
            )

    # --- Tracking sessions ---
    async def create_tracking_session(self, guild_id, started_by, interval, is_test=False):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                INSERT INTO tracking_sessions (guild_id, started_by, start_time, interval_seconds, is_test, active)
                VALUES ($1, $2, NOW(), $3, $4, TRUE)
                RETURNING *
            """, guild_id, started_by, interval, is_test)

    async def end_tracking_session(self, session_id):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE tracking_sessions SET active=FALSE, end_time=NOW()
                WHERE id=$1
            """, session_id)
