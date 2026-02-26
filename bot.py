import os
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio

# ---------------- ENV ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

MARKET_CHANNEL_ID = 1475144850826592267

# ---------------- DISCORD SETUP ----------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ---------------- SUPABASE HELPERS ----------------
async def supabase_get(session, table, params=""):
    url = f"{SUPABASE_URL}/rest/v1/{table}{params}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    async with session.get(url, headers=headers) as res:
        return await res.json(), res.status

async def supabase_post(session, table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "return=representation",
        "Content-Type": "application/json"
    }
    async with session.post(url, headers=headers, json=data) as res:
        return await res.json(), res.status

async def supabase_patch(session, table, params, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}{params}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    async with session.patch(url, headers=headers, json=data) as res:
        return await res.json(), res.status

# ============================================================
# /link CODE — Discord side of account linking
# ============================================================
@tree.command(name="link", description="Link your Discord account to your Minecraft account")
async def link(interaction: discord.Interaction, code: str):
    await interaction.response.defer(ephemeral=True)

    async with aiohttp.ClientSession() as session:
        # 1. Look up link code
        data, status = await supabase_get(session, "link_codes", f"?code=eq.{code}&used=eq.false")

        if status != 200 or len(data) == 0:
            return await interaction.followup.send("❌ Invalid or already used link code.", ephemeral=True)

        entry = data[0]
        mc_uuid = entry["mc_uuid"]

        # 2. Insert/update accounts table
        account_data = {
            "mc_uuid": mc_uuid,
            "discord_id": str(interaction.user.id)
        }

        await supabase_post(session, "accounts", account_data)

        # 3. Mark code as used
        await supabase_patch(session, "link_codes", f"?code=eq.{code}", {
            "used": True,
            "discord_id": str(interaction.user.id)
        })

        await interaction.followup.send("✅ Your Discord account is now linked to your Minecraft account!", ephemeral=True)

# ============================================================
# /market — View active marketplace listings
# ============================================================
@tree.command(name="market", description="View the global marketplace")
async def market(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    async with aiohttp.ClientSession() as session:
        data, status = await supabase_get(
            session,
            "marketplace_listings",
            "?status=eq.active&select=id,item_type,amount,price"
        )

        if status != 200:
            return await interaction.followup.send("❌ Failed to load marketplace.", ephemeral=True)

        if len(data) == 0:
            return await interaction.followup.send("📭 Marketplace is empty.", ephemeral=True)

        msg = "**🛒 Marketplace Listings**\n"
        for row in data:
            msg += f"**#{row['id']}** — {row['amount']}x `{row['item_type']}` for **{row['price']}**\n"

        await interaction.followup.send(msg, ephemeral=True)

# ============================================================
# /buy ID — Buy a listing
# ============================================================
@tree.command(name="buy", description="Buy a marketplace listing")
async def buy(interaction: discord.Interaction, listing_id: int):
    await interaction.response.defer(ephemeral=True)

    async with aiohttp.ClientSession() as session:
        # 1. Fetch listing
        data, status = await supabase_get(
            session,
            "marketplace_listings",
            f"?id=eq.{listing_id}"
        )

        if status != 200 or len(data) == 0:
            return await interaction.followup.send("❌ Listing not found.", ephemeral=True)

        listing = data[0]

        if listing["status"] != "active":
            return await interaction.followup.send("❌ This listing is no longer available.", ephemeral=True)

        # 2. Get buyer's linked MC UUID
        acc, acc_status = await supabase_get(
            session,
            "accounts",
            f"?discord_id=eq.{interaction.user.id}"
        )

        if acc_status != 200 or len(acc) == 0:
            return await interaction.followup.send("❌ You must link your account first using `/link CODE`.", ephemeral=True)

        buyer_mc_uuid = acc[0]["mc_uuid"]

        # 3. Mark listing as sold
        await supabase_patch(
            session,
            "marketplace_listings",
            f"?id=eq.{listing_id}",
            {
                "status": "sold",
                "buyer_mc_uuid": buyer_mc_uuid
            }
        )

        await interaction.followup.send(
            f"✅ You bought **{listing['amount']}x {listing['item_type']}**.\n"
            f"It will be delivered next time you join Minecraft.",
            ephemeral=True
        )

# ============================================================
# /sell — Discord-side listing (optional)
# ============================================================
@tree.command(name="sell", description="List an item on the marketplace (Discord-side)")
async def sell(interaction: discord.Interaction, item: str, amount: int, price: float):
    await interaction.response.defer(ephemeral=True)

    async with aiohttp.ClientSession() as session:
        # Get linked MC UUID
        acc, acc_status = await supabase_get(
            session,
            "accounts",
            f"?discord_id=eq.{interaction.user.id}"
        )

        if acc_status != 200 or len(acc) == 0:
            return await interaction.followup.send("❌ You must link your account first.", ephemeral=True)

        mc_uuid = acc[0]["mc_uuid"]

        # Create listing
        listing_data = {
            "seller_mc_uuid": mc_uuid,
            "item_type": item.upper(),
            "amount": amount,
            "price": price,
            "status": "active"
        }

        created, status = await supabase_post(session, "marketplace_listings", listing_data)

        if status != 201:
            return await interaction.followup.send("❌ Failed to create listing.", ephemeral=True)

        listing_id = created[0]["id"]

        # Send embed to marketplace channel
        channel = bot.get_channel(MARKET_CHANNEL_ID)
        if channel:
            embed = discord.Embed(title="📦 New Marketplace Listing", color=discord.Color.green())
            embed.add_field(name="Item", value=item.upper(), inline=True)
            embed.add_field(name="Amount", value=str(amount), inline=True)
            embed.add_field(name="Price", value=str(price), inline=True)
            embed.add_field(name="Listing ID", value=str(listing_id), inline=True)
            await channel.send(embed=embed)

        await interaction.followup.send(f"📦 Listed {amount}x {item} for {price}.", ephemeral=True)

# ============================================================
# STARTUP
# ============================================================
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online as {bot.user}")

bot.run(DISCORD_TOKEN)
