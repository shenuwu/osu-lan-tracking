# osu! LAN Tracker Bot

Discord bot voor het tracken van osu! scores tijdens een LAN, met mappool leaderboards, rankings en speler-vergelijkingen.

## Setup

### 1. Vereisten
- Python 3.11+
- PostgreSQL database (Railway biedt dit gratis aan)
- osu! API v2 credentials (client ID + secret)
- Discord bot token

### 2. osu! API credentials aanmaken
1. Ga naar https://osu.ppy.sh/home/account/edit
2. Scroll naar **OAuth** → klik **New OAuth Application**
3. Naam: `LAN Tracker`, callback URL: laat leeg
4. Kopieer je **Client ID** en **Client Secret**

### 3. Discord bot aanmaken
1. Ga naar https://discord.com/developers/applications
2. **New Application** → naam naar keuze
3. Ga naar **Bot** → **Add Bot**
4. Kopieer de **Token**
5. Onder **Privileged Gateway Intents**: zet **Server Members Intent** aan
6. Ga naar **OAuth2 → URL Generator**: kies `bot` + `applications.commands`
7. Permissions: `Send Messages`, `Manage Channels`, `Manage Messages`, `Read Message History`, `Embed Links`
8. Kopieer de gegenereerde URL en voeg de bot toe aan je server

### 4. Railway deployment
1. Push de code naar een GitHub repo
2. Maak een nieuw Railway project
3. **New Service** → GitHub repo
4. Voeg een **PostgreSQL** plugin toe aan het project
5. Stel de environment variables in (zie hieronder)
6. Railway detecteert automatisch de `Procfile` en start de worker

### 5. Environment variables (Railway → Variables)
```
DISCORD_TOKEN=        # Discord bot token
OSU_CLIENT_ID=        # osu! OAuth client ID
OSU_CLIENT_SECRET=    # osu! OAuth client secret
DATABASE_URL=         # Wordt automatisch ingesteld door Railway PostgreSQL plugin
```

---

## Commands

### 👤 Speler commands (iedereen)
| Command | Beschrijving |
|---|---|
| `/register <osu_username>` | Koppel je Discord aan je osu! account |
| `/unregister` | Verwijder jezelf uit de tracker |
| `/profile` | Bekijk je eigen LAN stats |
| `/recent [member] [limit]` | Recentste scores |
| `/compare <member>` | Vergelijk je stats met iemand anders |
| `/pool_scores <pool_channel>` | Jouw scores in een specifieke pool |

### 🏆 Stats commands (iedereen)
| Command | Beschrijving |
|---|---|
| `/leaderboard` | Algemeen LAN leaderboard op totale score |
| `/rankings <stat>` | Rankings op accuracy, maps, fc count, etc. |

### 🔧 Admin commands
| Command | Beschrijving |
|---|---|
| `/add_player <member> <osu_username>` | Voeg speler handmatig toe |
| `/remove_player <member>` | Verwijder speler |
| `/list_players` | Bekijk alle geregistreerde spelers |
| `/create_pool <name> [category]` | Maak een mappool channel aan |
| `/add_map <pool_channel> <beatmap_id> <slot>` | Voeg map toe aan pool (bijv. `NM1`) |
| `/remove_map <pool_channel> <beatmap_id>` | Verwijder map uit pool |
| `/refresh_leaderboard <pool_channel>` | Herlaad leaderboard van een pool |
| `/start_tracking [interval] [timeframe_hours]` | Start score polling |
| `/stop_tracking` | Stop score polling |
| `/test_tracking` | Test of de API werkt (geen opslag) |
| `/set_score_channel <channel>` | Channel voor score notificaties |
| `/tracking_status` | Huidige tracking status |

---

## Hoe het werkt

1. **Registratie**: Spelers koppelen hun Discord aan hun osu! account via `/register`, of een admin doet dit via `/add_player`.
2. **Tracking**: `/start_tracking` start een achtergrond loop die elke N seconden recente scores ophaalt voor alle geregistreerde spelers via de osu! API v2.
3. **Pool leaderboards**: Maak een pool aan met `/create_pool`, voeg beatmaps toe met `/add_map <slot>`. Zodra een speler een score zet op een poolmap, wordt het leaderboard automatisch bijgewerkt.
4. **Stats**: Gebruik `/leaderboard`, `/rankings` en `/compare` voor LAN-brede statistieken.

---

## Lokaal draaien (voor development)
```bash
pip install -r requirements.txt
cp .env.example .env
# Vul .env in
python bot.py
```
