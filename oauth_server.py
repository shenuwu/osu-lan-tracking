"""
Kleine aiohttp webserver die de osu! OAuth callback afhandelt.
Slaat één bot-level OAuth token op die gebruikt wordt voor alle score lookups.
"""
import os
import logging
import aiohttp
from aiohttp import web
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("oauth")

OSU_TOKEN_URL = "https://osu.ppy.sh/oauth/token"
OSU_USER_URL  = "https://osu.ppy.sh/api/v2/me/osu"


async def handle_callback(request: web.Request) -> web.Response:
    code  = request.rel_url.query.get("code")
    state = request.rel_url.query.get("state")

    if not code:
        return web.Response(text="Ongeldige callback — code ontbreekt.", status=400)

    client_id     = os.getenv("OSU_OAUTH_CLIENT_ID") or os.getenv("OSU_CLIENT_ID")
    client_secret = os.getenv("OSU_OAUTH_CLIENT_SECRET") or os.getenv("OSU_CLIENT_SECRET")
    redirect_uri  = os.getenv("OSU_REDIRECT_URI")

    logger.info(f"Token exchange: client_id={client_id} redirect_uri={redirect_uri} code={code[:10]}...")

    payload = {
        "client_id":     client_id,
        "client_secret": client_secret,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  redirect_uri,
    }
    logger.info(f"Payload (zonder secret): { {k:v for k,v in payload.items() if k != 'client_secret'} }")

    async with aiohttp.ClientSession() as session:
        resp = await session.post(OSU_TOKEN_URL, json=payload)
        if resp.status != 200:
            text = await resp.text()
            logger.error(f"Token exchange gefaald: {resp.status} {text}")
            return web.Response(text="Token exchange gefaald. Probeer opnieuw via /bot_link.", status=500)

        token_data = await resp.json()
        access_token  = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        expires_in    = token_data.get("expires_in", 86400)
        expires_at    = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        headers = {"Authorization": f"Bearer {access_token}"}
        user_resp = await session.get(OSU_USER_URL, headers=headers)
        user_data = await user_resp.json() if user_resp.status == 200 else {}

    # Sla op als bot-level token (discord_id=0 = bot token)
    db = request.app["db"]
    await db.save_oauth_token(0, user_data.get("id", 0), access_token, refresh_token, expires_at)
    logger.info(f"Bot OAuth token opgeslagen voor osu! user: {user_data.get('username')}")

    return web.Response(
        text=f"✅ Bot gekoppeld als {user_data.get('username', '?')}! Je kunt dit venster sluiten.",
        content_type="text/html"
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app(db) -> web.Application:
    app = web.Application()
    app["db"] = db
    app.router.add_get("/callback", handle_callback)
    app.router.add_get("/health",   handle_health)
    return app


async def start_oauth_server(db):
    port = int(os.getenv("PORT", 8080))
    app  = create_app(db)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"OAuth callback server draait op port {port}")
    return runner
