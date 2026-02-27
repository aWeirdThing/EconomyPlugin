import os
import discord
from discord.ext import commands
import aiohttp

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

MARKET_CHANNEL_ID = 1475144850826592267

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
        data = await res.json()
        return data, res.status

async def supabase_post(session, table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "return=representation",
        "Content-Type": "application/json"
    }
    async with session.post(url, headers=headers, json=data) as res:
        data = await res.json()
        return data, res.status

async def supabase_patch(session, table, params, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}{params}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    async with session.patch(url, headers=headers, json=data) as res:
        data = await res.json()
        return data, res.status

# ---------------- ACCOUNT HELPERS ----------------
async def get_account_by_discord(session, discord_id: int):
    data, status = await supabase_get(
        session,
        "accounts",
        f"?discord_id=eq.{discord_id}"
    )
    if status != 200 or len(data) == 0:
        return None
    return data[0]

async def get_account_by_mc_uuid(session, mc_uuid: str):
    data, status = await supabase_get(
        session,
        "accounts",
        f"?mc_uuid=eq.{mc_uuid}"
    )
    if status != 200 or len(data) == 0:
        return None
    return data[0]

async def update_account_balance(session, discord_id: int, new_balance: float):
    await supabase_patch(
        session,
        "accounts",
        f"?discord_id=eq.{discord_id}",
        {"balance": new_balance}
    )

# ============================================================
# /link CODE — link Discord ↔ Minecraft
# ============================================================
@tree.command(name="link", description="Link your Discord account to your Minecraft account")
async def link(interaction: discord.Interaction, code: str):
    await interaction.response.defer(thinking=True)

    async with aiohttp.ClientSession() as session:
        data, status = await supabase_get(
            session,
            "link_codes",
            f"?code=eq.{code}&used=eq.false"
        )

        if status != 200 or len(data) == 0:
            return await interaction.followup.send("❌ Invalid or already used link code.")

        entry = data[0]
        mc_uuid = entry["mc_uuid"]

        existing = await get_account_by_discord(session, interaction.user.id)

        if existing:
            await supabase_patch(
                session,
                "accounts",
                f"?discord_id=eq.{interaction.user.id}",
                {"mc_uuid": mc_uuid}
            )
        else:
            account_data = {
                "mc_uuid": mc_uuid,
                "discord_id": str(interaction.user.id)
                # balance uses DB default 100 WeirdCoins
            }
            await supabase_post(session, "accounts", account_data)

        await supabase_patch(
            session,
            "link_codes",
            f"?code=eq.{code}",
            {
                "used": True,
                "discord_id": str(interaction.user.id)
            }
        )

        await interaction.followup.send(
            f"✅ {interaction.user.mention}, your Discord account is now linked to your Minecraft account!\n"
            f"You start with **100 WeirdCoins**."
        )

# ============================================================
# /balance — show WeirdCoins
# ============================================================
@tree.command(name="balance", description="Check your WeirdCoins balance")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    async with aiohttp.ClientSession() as session:
        acc = await get_account_by_discord(session, interaction.user.id)
        if not acc:
            return await interaction.followup.send(
                "❌ You are not linked yet.\n"
                "Run `/link` in Minecraft to get a code, then `/link CODE` here."
            )

        bal = float(acc.get("balance", 0))
        await interaction.followup.send(
            f"💰 {interaction.user.mention}, you have **{bal:.2f} WeirdCoins**."
        )

# ============================================================
# /market — view active listings
# ============================================================
@tree.command(name="market", description="View the global marketplace")
async def market(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    async with aiohttp.ClientSession() as session:
        data, status = await supabase_get(
            session,
            "marketplace_listings",
            "?status=eq.active&select=id,item_type,amount,price"
        )

        if status != 200:
            return await interaction.followup.send("❌ Failed to load marketplace.")

        if len(data) == 0:
            return await interaction.followup.send("📭 Marketplace is empty.")

        msg = "**🛒 Marketplace Listings**\n"
        for row in data:
            msg += (
                f"**#{row['id']}** — {row['amount']}x "
                f"`{row['item_type']}` for **{row['price']} WeirdCoins**\n"
            )

        await interaction.followup.send(msg)

# ============================================================
# /buy ID — buy listing with WeirdCoins
# ============================================================
@tree.command(name="buy", description="Buy a marketplace listing")
async def buy(interaction: discord.Interaction, listing_id: int):
    await interaction.response.defer(thinking=True)

    async with aiohttp.ClientSession() as session:
        data, status = await supabase_get(
            session,
            "marketplace_listings",
            f"?id=eq.{listing_id}"
        )

        if status != 200 or len(data) == 0:
            return await interaction.followup.send("❌ Listing not found.")

        listing = data[0]

        if listing["status"] != "active":
            return await interaction.followup.send("❌ This listing is no longer available.")

        price = float(listing["price"])
        amount = int(listing["amount"])
        total_cost = price * amount

        buyer_acc = await get_account_by_discord(session, interaction.user.id)
        if not buyer_acc:
            return await interaction.followup.send(
                "❌ You must link your account first using `/link CODE`."
            )

        buyer_balance = float(buyer_acc.get("balance", 0))
        if buyer_balance < total_cost:
            return await interaction.followup.send(
                f"❌ Not enough WeirdCoins. You need **{total_cost:.2f}**, "
                f"but you have **{buyer_balance:.2f}**."
            )

        seller_mc_uuid = listing["seller_mc_uuid"]
        seller_acc = await get_account_by_mc_uuid(session, seller_mc_uuid)

        new_buyer_balance = buyer_balance - total_cost
        await update_account_balance(session, int(buyer_acc["discord_id"]), new_buyer_balance)

        if seller_acc:
            seller_balance = float(seller_acc.get("balance", 0))
            new_seller_balance = seller_balance + total_cost
            await update_account_balance(session, int(seller_acc["discord_id"]), new_seller_balance)

        # Mark listing as sold (pulled from sale)
        await supabase_patch(
            session,
            "marketplace_listings",
            f"?id=eq.{listing_id}",
            {
                "status": "sold",
                "buyer_mc_uuid": buyer_acc["mc_uuid"]
            }
        )

        await interaction.followup.send(
            f"✅ {interaction.user.mention} bought **{amount}x {listing['item_type']}** "
            f"for **{total_cost:.2f} WeirdCoins**.\n"
            f"New balance: **{new_buyer_balance:.2f} WeirdCoins**.\n"
            f"It will be delivered next time you join Minecraft or run `/deliver`."
        )

# ============================================================
# /sell — Discord-side listing
# ============================================================
@tree.command(name="sell", description="List an item on the marketplace (Discord-side)")
async def sell(interaction: discord.Interaction, item: str, amount: int, price: float):
    await interaction.response.defer(thinking=True)

    async with aiohttp.ClientSession() as session:
        acc = await get_account_by_discord(session, interaction.user.id)
        if not acc:
            return await interaction.followup.send("❌ You must link your account first.")

        mc_uuid = acc["mc_uuid"]

        listing_data = {
            "seller_mc_uuid": mc_uuid,
            "item_type": item.upper(),
            "amount": amount,
            "price": price,
            "status": "active"
        }

        created, status = await supabase_post(session, "marketplace_listings", listing_data)

        if status != 201:
            return await interaction.followup.send("❌ Failed to create listing.")

        listing_id = created[0]["id"]

        channel = bot.get_channel(MARKET_CHANNEL_ID)
        if channel:
            embed = discord.Embed(title="📦 New Marketplace Listing", color=discord.Color.green())
            embed.add_field(name="Seller", value=interaction.user.mention, inline=False)
            embed.add_field(name="Item", value=item.upper(), inline=True)
            embed.add_field(name="Amount", value=str(amount), inline=True)
            embed.add_field(name="Price", value=f"{price} WeirdCoins", inline=True)
            embed.add_field(name="Listing ID", value=str(listing_id), inline=False)
            await channel.send(embed=embed)

        await interaction.followup.send(
            f"📦 Listed **{amount}x {item.upper()}** for **{price} WeirdCoins** "
            f"as listing **#{listing_id}**."
        )

# ============================================================
# STARTUP
# ============================================================
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online as {bot.user}")

bot.run(DISCORD_TOKEN)
