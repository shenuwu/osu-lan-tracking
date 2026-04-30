import asyncpg
import os
from datetime import datetime, timezone


def _utc(dt: datetime) -> datetime:
    if dt is None:
        return datetime.utcnow()
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class Database:
    def __init__(self):
        self.pool = None

    async def init(self):
        self.pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))
        await self._create_tables()
        print("Database verbonden en tabellen aangemaakt")

    async def _create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    discord_id   BIGINT PRIMARY KEY,
                    osu_username TEXT NOT NULL,
                    osu_id       BIGINT NOT NULL UNIQUE,
                    added_by     BIGINT,
                    added_at     TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS pools (
                    id         SERIAL PRIMARY KEY,
                    name       TEXT NOT NULL,
                    channel_id BIGINT UNIQUE,
                    guild_id   BIGINT NOT NULL,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    active     BOOLEAN DEFAULT TRUE
                );

                -- Elke map in een pool, met de vereiste mod categorie (NM/HD/HR/DT)
                CREATE TABLE IF NOT EXISTS pool_maps (
                    id            SERIAL PRIMARY KEY,
                    pool_id       INT REFERENCES pools(id) ON DELETE CASCADE,
                    beatmap_id    BIGINT NOT NULL,
                    beatmapset_id BIGINT,
                    title         TEXT,
                    artist        TEXT,
                    version       TEXT,
                    slot          TEXT,   -- bijv. NM1, HD2, HR1, DT3
                    mod_category  TEXT,   -- NM, HD, HR, DT
                    UNIQUE(pool_id, beatmap_id)
                );

                -- Alle scores (stable + lazer), raw opgeslagen
                CREATE TABLE IF NOT EXISTS scores (
                    id            SERIAL PRIMARY KEY,
                    osu_score_id  BIGINT UNIQUE NOT NULL,
                    osu_id        BIGINT NOT NULL,
                    discord_id    BIGINT REFERENCES players(discord_id),
                    beatmap_id    BIGINT NOT NULL,
                    score         BIGINT NOT NULL,
                    accuracy      FLOAT NOT NULL,
                    max_combo     INT NOT NULL,
                    mods          TEXT DEFAULT 'NM',
                    mods_list     TEXT DEFAULT '[]',   -- JSON array van mod acronyms
                    rank          TEXT,
                    count_300     INT DEFAULT 0,
                    count_100     INT DEFAULT 0,
                    count_50      INT DEFAULT 0,
                    count_miss    INT DEFAULT 0,
                    pp            FLOAT DEFAULT 0,
                    is_pass       BOOLEAN DEFAULT TRUE,
                    client_type   TEXT DEFAULT 'stable',  -- 'stable' of 'lazer'
                    has_nf        BOOLEAN DEFAULT FALSE,
                    is_pool_score BOOLEAN DEFAULT FALSE,  -- zit deze map in een pool?
                    pool_id       INT REFERENCES pools(id),
                    pool_slot     TEXT,
                    is_valid      BOOLEAN DEFAULT TRUE,
                    invalid_reason TEXT,
                    submitted_at  TIMESTAMPTZ NOT NULL,
                    tracked_at    TIMESTAMPTZ DEFAULT NOW()
                );

                -- Beste pool score per speler per map (voor live leaderboard)
                CREATE TABLE IF NOT EXISTS pool_leaderboard (
                    id         SERIAL PRIMARY KEY,
                    pool_id    INT REFERENCES pools(id) ON DELETE CASCADE,
                    beatmap_id BIGINT NOT NULL,
                    discord_id BIGINT REFERENCES players(discord_id),
                    score_id   INT REFERENCES scores(id),
                    score      BIGINT NOT NULL,
                    accuracy   FLOAT NOT NULL,
                    mods       TEXT,
                    rank       TEXT,
                    count_miss INT DEFAULT 0,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(pool_id, beatmap_id, discord_id)
                );

                CREATE TABLE IF NOT EXISTS tracking_sessions (
                    id               SERIAL PRIMARY KEY,
                    guild_id         BIGINT NOT NULL,
                    started_by       BIGINT,
                    start_time       TIMESTAMPTZ NOT NULL,
                    end_time         TIMESTAMPTZ,
                    interval_seconds INT DEFAULT 60,
                    active           BOOLEAN DEFAULT TRUE
                );

                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id            BIGINT PRIMARY KEY,
                    score_channel_id    BIGINT,
                    log_channel_id      BIGINT,
                    tracking_active     BOOLEAN DEFAULT FALSE,
                    tracking_session_id INT REFERENCES tracking_sessions(id)
                );
            """)

            # Migraties voor bestaande databases
            for col, definition in [
                ("client_type",    "TEXT DEFAULT 'stable'"),
                ("has_nf",         "BOOLEAN DEFAULT FALSE"),
                ("mods_list",      "TEXT DEFAULT '[]'"),
                ("is_pool_score",  "BOOLEAN DEFAULT FALSE"),
                ("pool_id",        "INT"),
                ("pool_slot",      "TEXT"),
                ("is_valid",       "BOOLEAN DEFAULT TRUE"),
                ("invalid_reason", "TEXT"),
            ]:
                await conn.execute(
                    f"ALTER TABLE scores ADD COLUMN IF NOT EXISTS {col} {definition}"
                )
            await conn.execute(
                "ALTER TABLE pool_maps ADD COLUMN IF NOT EXISTS mod_category TEXT"
            )
            await conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS log_channel_id BIGINT"
            )
            await conn.execute(
                "ALTER TABLE pools ADD COLUMN IF NOT EXISTS leaderboard_message_id BIGINT"
            )

    # ── Players ─────────────────────────────────────────────────────────────

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

    # ── Pools ────────────────────────────────────────────────────────────────

    async def create_pool(self, name, channel_id, guild_id, created_by):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                INSERT INTO pools (name, channel_id, guild_id, created_by)
                VALUES ($1, $2, $3, $4)
                RETURNING *
            """, name, channel_id, guild_id, created_by)

    async def delete_pool(self, pool_id):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM pools WHERE id=$1", pool_id)

    async def get_pool_by_channel(self, channel_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM pools WHERE channel_id=$1", channel_id)

    async def get_pool_by_id(self, pool_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM pools WHERE id=$1", pool_id)

    async def save_leaderboard_message_id(self, pool_id: int, message_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE pools SET leaderboard_message_id=$2 WHERE id=$1",
                pool_id, message_id
            )

    async def get_all_pools(self, guild_id):
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM pools WHERE guild_id=$1 ORDER BY created_at", guild_id
            )

    # ── Pool maps ────────────────────────────────────────────────────────────

    async def add_map_to_pool(self, pool_id, beatmap_id, beatmapset_id, title, artist, version, slot, mod_category):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO pool_maps (pool_id, beatmap_id, beatmapset_id, title, artist, version, slot, mod_category)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (pool_id, beatmap_id) DO UPDATE
                SET slot=$7, mod_category=$8
            """, pool_id, beatmap_id, beatmapset_id, title, artist, version, slot, mod_category)

    async def remove_map_from_pool(self, pool_id, beatmap_id):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pool_maps WHERE pool_id=$1 AND beatmap_id=$2", pool_id, beatmap_id
            )

    async def get_pool_maps(self, pool_id):
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM pool_maps WHERE pool_id=$1 ORDER BY mod_category, slot", pool_id
            )

    async def get_pool_map(self, pool_id, beatmap_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT * FROM pool_maps WHERE pool_id=$1 AND beatmap_id=$2", pool_id, beatmap_id
            )

    async def get_all_pool_map_ids(self):
        """Alle beatmap IDs in alle pools, voor tracking filter."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT DISTINCT beatmap_id, pool_id, slot, mod_category FROM pool_maps")
            return {r["beatmap_id"]: r for r in rows}

    # ── Scores ───────────────────────────────────────────────────────────────

    async def score_exists(self, osu_score_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id FROM scores WHERE osu_score_id=$1", osu_score_id)
            return row is not None

    async def save_score(self, data: dict):
        """Insert score. Returnt (score_id, is_new). score_id is altijd gevuld als de score bestaat."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO scores (
                    osu_score_id, osu_id, discord_id, beatmap_id,
                    score, accuracy, max_combo, mods, mods_list, rank,
                    count_300, count_100, count_50, count_miss, pp,
                    is_pass, client_type, has_nf,
                    is_pool_score, pool_id, pool_slot,
                    is_valid, invalid_reason, submitted_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
                    $11,$12,$13,$14,$15,$16,$17,$18,
                    $19,$20,$21,$22,$23,$24
                )
                ON CONFLICT (osu_score_id) DO NOTHING
                RETURNING id
            """,
                data["osu_score_id"], data["osu_id"], data.get("discord_id"),
                data["beatmap_id"], data["score"], data["accuracy"],
                data["max_combo"], data["mods"], data.get("mods_list", "[]"),
                data["rank"], data["count_300"], data["count_100"],
                data["count_50"], data["count_miss"], data.get("pp", 0),
                data["is_pass"], data.get("client_type", "stable"),
                data.get("has_nf", False),
                data.get("is_pool_score", False), data.get("pool_id"),
                data.get("pool_slot"),
                data.get("is_valid", True), data.get("invalid_reason"),
                _utc(data["submitted_at"])
            )
            if row:
                return row["id"], True
            # Score bestond al — haal id op voor leaderboard update
            existing = await conn.fetchrow(
                "SELECT id FROM scores WHERE osu_score_id=$1", data["osu_score_id"]
            )
            return (existing["id"] if existing else None), False

    async def update_pool_leaderboard(self, pool_id, beatmap_id, discord_id, score_row_id, score, accuracy, mods, rank, count_miss):
        """Vervang leaderboard entry als de nieuwe score hoger is."""
        async with self.pool.acquire() as conn:
            existing = await conn.fetchrow("""
                SELECT id, score FROM pool_leaderboard
                WHERE pool_id=$1 AND beatmap_id=$2 AND discord_id=$3
            """, pool_id, beatmap_id, discord_id)

            if existing is None:
                await conn.execute("""
                    INSERT INTO pool_leaderboard
                        (pool_id, beatmap_id, discord_id, score_id, score, accuracy, mods, rank, count_miss)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """, pool_id, beatmap_id, discord_id, score_row_id, score, accuracy, mods, rank, count_miss)
                return True  # nieuw
            elif score > existing["score"]:
                await conn.execute("""
                    UPDATE pool_leaderboard
                    SET score_id=$4, score=$5, accuracy=$6, mods=$7, rank=$8, count_miss=$9, updated_at=NOW()
                    WHERE pool_id=$1 AND beatmap_id=$2 AND discord_id=$3
                """, pool_id, beatmap_id, discord_id, score_row_id, score, accuracy, mods, rank, count_miss)
                return True  # verbeterd
            return False  # niet verbeterd

    # ── Leaderboard queries ──────────────────────────────────────────────────

    async def get_pool_leaderboard(self, pool_id):
        """Per map: alle spelers gesorteerd op score, alleen hun beste."""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT
                    pl.beatmap_id, pl.score, pl.accuracy, pl.mods, pl.rank, pl.count_miss,
                    pl.updated_at,
                    p.osu_username, p.discord_id,
                    pm.title, pm.artist, pm.version, pm.slot, pm.mod_category
                FROM pool_leaderboard pl
                JOIN players p ON p.discord_id = pl.discord_id
                JOIN pool_maps pm ON pm.pool_id = pl.pool_id AND pm.beatmap_id = pl.beatmap_id
                WHERE pl.pool_id = $1
                ORDER BY pm.mod_category, pm.slot, pl.score DESC
            """, pool_id)

    async def get_map_leaderboard(self, pool_id, beatmap_id):
        """Leaderboard van 1 specifieke map."""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT
                    pl.score, pl.accuracy, pl.mods, pl.rank, pl.count_miss, pl.updated_at,
                    p.osu_username, p.discord_id
                FROM pool_leaderboard pl
                JOIN players p ON p.discord_id = pl.discord_id
                WHERE pl.pool_id=$1 AND pl.beatmap_id=$2
                ORDER BY pl.score DESC
            """, pool_id, beatmap_id)

    async def get_global_leaderboard(self, guild_id):
        """Einde-LAN ranking: totaal score over alle pool_leaderboard entries per speler."""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT
                    p.osu_username, p.discord_id,
                    COUNT(DISTINCT pl.beatmap_id)         AS maps_completed,
                    SUM(pl.score)                          AS total_score,
                    AVG(pl.accuracy)                       AS avg_accuracy,
                    SUM(CASE WHEN pl.count_miss = 0 THEN 1 ELSE 0 END) AS fc_count
                FROM players p
                LEFT JOIN pool_leaderboard pl ON pl.discord_id = p.discord_id
                JOIN pools po ON po.id = pl.pool_id AND po.guild_id = $1
                GROUP BY p.osu_username, p.discord_id
                ORDER BY total_score DESC NULLS LAST
            """, guild_id)

    async def get_player_pool_scores(self, discord_id, pool_id):
        """Alle pool scores van 1 speler in 1 pool."""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT
                    pl.score, pl.accuracy, pl.mods, pl.rank, pl.count_miss, pl.updated_at,
                    pm.title, pm.artist, pm.version, pm.slot, pm.mod_category
                FROM pool_leaderboard pl
                JOIN pool_maps pm ON pm.pool_id = pl.pool_id AND pm.beatmap_id = pl.beatmap_id
                WHERE pl.discord_id=$1 AND pl.pool_id=$2
                ORDER BY pm.mod_category, pm.slot
            """, discord_id, pool_id)

    async def get_player_all_scores(self, discord_id, limit=50, client_type=None):
        """Alle scores van een speler, optioneel gefilterd op client type."""
        async with self.pool.acquire() as conn:
            if client_type:
                return await conn.fetch("""
                    SELECT * FROM scores
                    WHERE discord_id=$1 AND client_type=$2
                    ORDER BY submitted_at DESC LIMIT $3
                """, discord_id, client_type, limit)
            return await conn.fetch("""
                SELECT * FROM scores
                WHERE discord_id=$1
                ORDER BY submitted_at DESC LIMIT $2
            """, discord_id, limit)

    async def get_player_stats_summary(self, discord_id):
        """Samenvatting stats voor /profile."""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT
                    COUNT(*)                                        AS total_scores,
                    COUNT(*) FILTER (WHERE client_type='lazer')     AS lazer_scores,
                    COUNT(*) FILTER (WHERE client_type='stable')    AS stable_scores,
                    COUNT(*) FILTER (WHERE is_pool_score=TRUE)      AS pool_scores,
                    AVG(accuracy) FILTER (WHERE is_pass=TRUE)       AS avg_accuracy,
                    MAX(score)                                       AS top_score,
                    SUM(CASE WHEN count_miss=0 AND is_pass=TRUE THEN 1 ELSE 0 END) AS fc_count,
                    COUNT(*) FILTER (WHERE is_pass=TRUE)            AS pass_count
                FROM scores WHERE discord_id=$1
            """, discord_id)

    async def get_player_pool_summary(self, discord_id, guild_id):
        """Hoeveel pool maps gespeeld per pool."""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT
                    po.name AS pool_name, po.id AS pool_id,
                    COUNT(pl.beatmap_id) AS maps_done,
                    (SELECT COUNT(*) FROM pool_maps WHERE pool_id=po.id) AS maps_total,
                    SUM(pl.score) AS total_score,
                    AVG(pl.accuracy) AS avg_accuracy
                FROM pools po
                LEFT JOIN pool_leaderboard pl ON pl.pool_id=po.id AND pl.discord_id=$1
                WHERE po.guild_id=$2
                GROUP BY po.id, po.name
                ORDER BY po.created_at
            """, discord_id, guild_id)

    async def get_recent_scores(self, discord_id, limit=5):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT s.*, pm.title, pm.slot, pm.mod_category
                FROM scores s
                LEFT JOIN pool_maps pm ON pm.beatmap_id = s.beatmap_id
                WHERE s.discord_id=$1
                ORDER BY s.submitted_at DESC
                LIMIT $2
            """, discord_id, limit)

    async def compare_players(self, discord_id_a, discord_id_b, guild_id):
        """Vergelijk twee spelers op pool scores."""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT
                    pm.slot, pm.mod_category, pm.title,
                    pla.score AS score_a, pla.accuracy AS acc_a,
                    plb.score AS score_b, plb.accuracy AS acc_b
                FROM pool_maps pm
                JOIN pools po ON po.id = pm.pool_id AND po.guild_id=$3
                LEFT JOIN pool_leaderboard pla ON pla.pool_id=pm.pool_id AND pla.beatmap_id=pm.beatmap_id AND pla.discord_id=$1
                LEFT JOIN pool_leaderboard plb ON plb.pool_id=pm.pool_id AND plb.beatmap_id=pm.beatmap_id AND plb.discord_id=$2
                ORDER BY pm.mod_category, pm.slot
            """, discord_id_a, discord_id_b, guild_id)

    # ── Guild settings ───────────────────────────────────────────────────────

    async def get_guild_settings(self, guild_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM guild_settings WHERE guild_id=$1", guild_id)
            if not row:
                await conn.execute(
                    "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id
                )
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

    # ── Tracking sessions ────────────────────────────────────────────────────

    async def create_tracking_session(self, guild_id, started_by, interval):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                INSERT INTO tracking_sessions (guild_id, started_by, start_time, interval_seconds, active)
                VALUES ($1, $2, NOW(), $3, TRUE)
                RETURNING *
            """, guild_id, started_by, interval)

    async def end_tracking_session(self, session_id):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE tracking_sessions SET active=FALSE, end_time=NOW() WHERE id=$1
            """, session_id)

    # ── Debug ────────────────────────────────────────────────────────────────

    async def get_all_scores_raw(self, limit=50):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT s.*, p.osu_username FROM scores s
                LEFT JOIN players p ON p.discord_id = s.discord_id
                ORDER BY s.tracked_at DESC LIMIT $1
            """, limit)
