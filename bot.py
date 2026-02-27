import os
import discord
from discord.ext import commands
import aiohttp
import re
import random

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

MARKET_CHANNEL_ID = 1475144850826592267

# ---------------- INTENTS ----------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ---------------- SAFE JSON PARSER ----------------
async def safe_json(res):
    try:
        return await res.json()
    except Exception:
        text = await res.text()
        return {"error": text}

# ---------------- SUPABASE HELPERS ----------------
async def supabase_get(session, table, params=""):
    url = f"{SUPABASE_URL}/rest/v1/{table}{params}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with session.get(url, headers=headers) as res:
        return await safe_json(res), res.status

async def supabase_post(session, table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "return=representation",
        "Content-Type": "application/json"
    }
    async with session.post(url, headers=headers, json=data) as res:
        return await safe_json(res), res.status

async def supabase_patch(session, table, params, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}{params}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    async with session.patch(url, headers=headers, json=data) as res:
        return await safe_json(res), res.status

# ---------------- ACCOUNT HELPERS ----------------
async def get_account_by_discord(session, discord_id: int):
    discord_id_str = str(discord_id)
    data, status = await supabase_get(session, "accounts", f"?discord_id=eq.{discord_id_str}")
    return data[0] if status == 200 and isinstance(data, list) and data else None

async def get_account_by_mc_uuid(session, mc_uuid: str):
    data, status = await supabase_get(session, "accounts", f"?mc_uuid=eq.{mc_uuid}")
    return data[0] if status == 200 and isinstance(data, list) and data else None

async def update_account_balance(session, discord_id: int, new_balance: float):
    await supabase_patch(
        session,
        "accounts",
        f"?discord_id=eq.{str(discord_id)}",
        {"balance": new_balance}
    )

# ---------------- TARGET PARSER (for admin) ----------------
def parse_target(target: str):
    if target.startswith("<@") and target.endswith(">"):
        return "discord", int(target.replace("<@", "").replace(">", "").replace("!", ""))
    if target.isdigit():
        return "discord", int(target)
    if re.match(r"^[0-9a-fA-F-]{32,36}$", target):
        return "mc", target
    return None, None

# ---------------- MARKET PAGINATION VIEW ----------------
class MarketView(discord.ui.View):
    def __init__(self, listings, per_page: int = 10):
        super().__init__(timeout=None)  # never times out
        self.listings = listings
        self.per_page = per_page
        self.page = 0

    def max_page(self) -> int:
        if not self.listings:
            return 0
        return (len(self.listings) - 1) // self.per_page

    def page_slice(self):
        start = self.page * self.per_page
        end = start + self.per_page
        return self.listings[start:end]

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🛒 Marketplace Listings",
            color=discord.Color.blurple()
        )

        if not self.listings:
            embed.description = "📭 Marketplace is empty."
            return embed

        for row in self.page_slice():
            line = (
                f"**#{row['id']}** — {row['amount']}x "
                f"`{row['item_type']}` for **{row['price']} WeirdCoins**"
            )
            embed.add_field(name="\u200b", value=line, inline=False)

        embed.set_footer(
            text=f"Page {self.page + 1}/{self.max_page() + 1} • {len(self.listings)} total listings"
        )
        return embed

    async def _update(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="⏮ First", style=discord.ButtonStyle.secondary, custom_id="market_first")
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        await self._update(interaction)

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="market_prev")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await self._update(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="market_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.max_page():
            self.page += 1
        await self._update(interaction)

    @discord.ui.button(label="Last ⏭", style=discord.ButtonStyle.secondary, custom_id="market_last")
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.max_page()
        await self._update(interaction)

# ---------------- BLACKJACK HELPERS ----------------
CARD_VALUES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
    "7": 7, "8": 8, "9": 9, "10": 10,
    "J": 10, "Q": 10, "K": 10, "A": 11
}
CARD_NAMES = list(CARD_VALUES.keys())

def draw_card():
    return random.choice(CARD_NAMES)

def hand_value(cards):
    total = sum(CARD_VALUES[c] for c in cards)
    aces = cards.count("A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total

def format_hand(cards):
    return ", ".join(cards) + f" (total: {hand_value(cards)})"

# ============================================================
# /link CODE — link Discord ↔ Minecraft
# ============================================================
@tree.command(name="link", description="Link your Discord account to your Minecraft account")
async def link(interaction: discord.Interaction, code: str):
    await interaction.response.defer(thinking=True)

    try:
        async with aiohttp.ClientSession() as session:
            data, status = await supabase_get(
                session,
                "link_codes",
                f"?code=eq.{code}&used=eq.false"
            )

            if status != 200 or not isinstance(data, list) or len(data) == 0:
                print("LINK: no matching code or bad status", status, data)
                return await interaction.followup.send("❌ Invalid or already used link code.")

            entry = data[0]
            mc_uuid = entry["mc_uuid"]

            existing = await get_account_by_discord(session, interaction.user.id)

            if existing:
                await supabase_patch(
                    session,
                    "accounts",
                    f"?discord_id=eq.{str(interaction.user.id)}",
                    {"mc_uuid": mc_uuid}
                )
            else:
                await supabase_post(
                    session,
                    "accounts",
                    {
                        "mc_uuid": mc_uuid,
                        "discord_id": str(interaction.user.id)
                        # balance uses DB default 100
                    }
                )

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
                f"✅ {interaction.user.mention}, your Discord account is now linked!\n"
                f"You start with **100 WeirdCoins** (if this is your first time)."
            )

    except Exception as e:
        print("LINK ERROR:", e)
        await interaction.followup.send("❌ Internal error during linking.")

# ============================================================
# /balance — show WeirdCoins
# ============================================================
@tree.command(name="balance", description="Check your WeirdCoins balance")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        async with aiohttp.ClientSession() as session:
            acc = await get_account_by_discord(session, interaction.user.id)
            if not acc:
                return await interaction.followup.send(
                    "❌ You are not linked.\n"
                    "Run `/link` in Minecraft to get a code, then `/link CODE` here."
                )

            bal = float(acc.get("balance", 0))
            await interaction.followup.send(
                f"💰 {interaction.user.mention}, you have **{bal:.2f} WeirdCoins**."
            )

    except Exception as e:
        print("BALANCE ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# /market — view active listings (PAGINATED)
# ============================================================
@tree.command(name="market", description="View the global marketplace")
async def market(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        async with aiohttp.ClientSession() as session:
            data, status = await supabase_get(
                session,
                "marketplace_listings",
                "?status=eq.active&select=id,item_type,amount,price"
            )

            if status != 200 or not isinstance(data, list):
                print("MARKET ERROR DATA:", status, data)
                return await interaction.followup.send("❌ Failed to load marketplace.")

            if len(data) == 0:
                return await interaction.followup.send("📭 Marketplace is empty.")

            view = MarketView(data, per_page=10)
            embed = view.build_embed()
            await interaction.followup.send(embed=embed, view=view)

    except Exception as e:
        print("MARKET ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# /buy ID — buy listing with WeirdCoins
# ============================================================
@tree.command(name="buy", description="Buy a marketplace listing")
async def buy(interaction: discord.Interaction, listing_id: int):
    await interaction.response.defer(thinking=True)

    try:
        async with aiohttp.ClientSession() as session:
            data, status = await supabase_get(
                session,
                "marketplace_listings",
                f"?id=eq.{listing_id}"
            )

            if status != 200 or not isinstance(data, list) or len(data) == 0:
                return await interaction.followup.send("❌ Listing not found.")

            listing = data[0]

            if listing["status"] != "active":
                return await interaction.followup.send("❌ This listing is no longer available.")

            price = float(listing["price"])
            amount = int(listing["amount"])
            total_cost = price * amount

            buyer_acc = await get_account_by_discord(session, interaction.user.id)
            if not buyer_acc:
                return await interaction.followup.send("❌ You must link your account first.")

            # Prevent buying your own listing
            if buyer_acc.get("mc_uuid") == listing.get("seller_mc_uuid"):
                return await interaction.followup.send("❌ You cannot buy your own listing.")

            buyer_balance = float(buyer_acc.get("balance", 0))
            if buyer_balance < total_cost:
                return await interaction.followup.send(
                    f"❌ You need **{total_cost:.2f}**, but you only have **{buyer_balance:.2f}**."
                )

            seller_acc = await get_account_by_mc_uuid(session, listing["seller_mc_uuid"])

            # Deduct from buyer
            await update_account_balance(
                session,
                int(buyer_acc["discord_id"]),
                buyer_balance - total_cost
            )

            # Pay seller if they have an account
            if seller_acc:
                seller_balance = float(seller_acc.get("balance", 0))
                await update_account_balance(
                    session,
                    int(seller_acc["discord_id"]),
                    seller_balance + total_cost
                )

            # Mark sold
            await supabase_patch(
                session,
                "marketplace_listings",
                f"?id=eq.{listing_id}",
                {"status": "sold", "buyer_mc_uuid": buyer_acc["mc_uuid"]}
            )

            await interaction.followup.send(
                f"✅ {interaction.user.mention} bought **{amount}x {listing['item_type']}** "
                f"for **{total_cost:.2f} WeirdCoins**.\n"
                f"It will be delivered next time you join Minecraft or run `/deliver`."
            )

    except Exception as e:
        print("BUY ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# /sell — Discord-side listing
# ============================================================
@tree.command(name="sell", description="List an item on the marketplace (Discord-side)")
async def sell(interaction: discord.Interaction, item: str, amount: int, price: float):
    await interaction.response.defer(thinking=True)

    try:
        async with aiohttp.ClientSession() as session:
            acc = await get_account_by_discord(session, interaction.user.id)
            if not acc:
                return await interaction.followup.send("❌ You must link your account first.")

            listing_data = {
                "seller_mc_uuid": acc["mc_uuid"],
                "item_type": item.upper(),
                "amount": amount,
                "price": price,
                "status": "active"
            }

            created, status = await supabase_post(session, "marketplace_listings", listing_data)

            if status != 201:
                print("SELL CREATE ERROR:", status, created)
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

    except Exception as e:
        print("SELL ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# ADMIN COMMANDS
# ============================================================
@tree.command(name="givemoney", description="Admin: Give WeirdCoins to a user or UUID")
async def givemoney(interaction: discord.Interaction, target: str, amount: float):
    await interaction.response.defer(thinking=True)

    if not interaction.user.guild_permissions.administrator:
        return await interaction.followup.send("❌ Admins only.")

    try:
        mode, value = parse_target(target)
        if mode is None:
            return await interaction.followup.send("❌ Invalid target. Use @user, Discord ID, or UUID.")

        async with aiohttp.ClientSession() as session:
            if mode == "discord":
                acc = await get_account_by_discord(session, value)
            else:
                acc = await get_account_by_mc_uuid(session, value)

            if not acc:
                return await interaction.followup.send("❌ Account not found.")

            new_balance = float(acc["balance"]) + amount
            await update_account_balance(session, int(acc["discord_id"]), new_balance)

            await interaction.followup.send(
                f"✅ Added **{amount} WeirdCoins**. New balance: **{new_balance:.2f}**."
            )

    except Exception as e:
        print("GIVEMONEY ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

@tree.command(name="removemoney", description="Admin: Remove WeirdCoins from a user or UUID")
async def removemoney(interaction: discord.Interaction, target: str, amount: float):
    await interaction.response.defer(thinking=True)

    if not interaction.user.guild_permissions.administrator:
        return await interaction.followup.send("❌ Admins only.")

    try:
        mode, value = parse_target(target)
        if mode is None:
            return await interaction.followup.send("❌ Invalid target. Use @user, Discord ID, or UUID.")

        async with aiohttp.ClientSession() as session:
            if mode == "discord":
                acc = await get_account_by_discord(session, value)
            else:
                acc = await get_account_by_mc_uuid(session, value)

            if not acc:
                return await interaction.followup.send("❌ Account not found.")

            new_balance = max(0, float(acc["balance"]) - amount)
            await update_account_balance(session, int(acc["discord_id"]), new_balance)

            await interaction.followup.send(
                f"✅ Removed **{amount} WeirdCoins**. New balance: **{new_balance:.2f}**."
            )

    except Exception as e:
        print("REMOVEMONEY ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# /leaderboard — top balances
# ============================================================
@tree.command(name="leaderboard", description="Show the top WeirdCoins holders")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        async with aiohttp.ClientSession() as session:
            data, status = await supabase_get(
                session,
                "accounts",
                "?select=discord_id,balance&order=balance.desc&limit=10"
            )

            if status != 200 or not isinstance(data, list):
                print("LEADERBOARD ERROR DATA:", status, data)
                return await interaction.followup.send("❌ Failed to load leaderboard.")

            if len(data) == 0:
                return await interaction.followup.send("📭 No accounts yet.")

            lines = []
            rank = 1
            for row in data:
                did = row["discord_id"]
                bal = float(row.get("balance", 0))
                user = bot.get_user(int(did)) or await bot.fetch_user(int(did))
                name = user.mention if user else f"`{did}`"
                lines.append(f"**#{rank}** — {name}: **{bal:.2f} WeirdCoins**")
                rank += 1

            msg = "**🏆 WeirdCoins Leaderboard**\n" + "\n".join(lines)
            await interaction.followup.send(msg)

    except Exception as e:
        print("LEADERBOARD ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# /profile — show your account info
# ============================================================
@tree.command(name="profile", description="Show your WeirdCoins profile")
async def profile(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        async with aiohttp.ClientSession() as session:
            acc = await get_account_by_discord(session, interaction.user.id)
            if not acc:
                return await interaction.followup.send(
                    "❌ You are not linked.\n"
                    "Run `/link` in Minecraft to get a code, then `/link CODE` here."
                )

            bal = float(acc.get("balance", 0))
            mc_uuid = acc.get("mc_uuid", "Not linked to Minecraft")

            embed = discord.Embed(
                title=f"{interaction.user.name}'s Profile",
                color=discord.Color.gold()
            )
            embed.add_field(name="WeirdCoins", value=f"**{bal:.2f}**", inline=False)
            embed.add_field(name="Minecraft UUID", value=f"`{mc_uuid}`", inline=False)

            await interaction.followup.send(embed=embed)

    except Exception as e:
        print("PROFILE ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# /richest — show the single richest player
# ============================================================
@tree.command(name="richest", description="Show the richest WeirdCoins holder")
async def richest(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    try:
        async with aiohttp.ClientSession() as session:
            data, status = await supabase_get(
                session,
                "accounts",
                "?select=discord_id,balance&order=balance.desc&limit=1"
            )

            if status != 200 or not isinstance(data, list):
                print("RICHEST ERROR DATA:", status, data)
                return await interaction.followup.send("❌ Failed to load richest player.")

            if len(data) == 0:
                return await interaction.followup.send("📭 No accounts yet.")

            row = data[0]
            did = row["discord_id"]
            bal = float(row.get("balance", 0))
            user = bot.get_user(int(did)) or await bot.fetch_user(int(did))
            name = user.mention if user else f"`{did}`"

            await interaction.followup.send(
                f"👑 Richest player: {name} with **{bal:.2f} WeirdCoins**."
            )

    except Exception as e:
        print("RICHEST ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# /transfer — send WeirdCoins to another player
# ============================================================
@tree.command(name="transfer", description="Send WeirdCoins to another player")
async def transfer(interaction: discord.Interaction, target: discord.User, amount: float):
    await interaction.response.defer(thinking=True)

    if target.id == interaction.user.id:
        return await interaction.followup.send("❌ You can't transfer to yourself.")

    if amount <= 0:
        return await interaction.followup.send("❌ Amount must be positive.")

    try:
        async with aiohttp.ClientSession() as session:
            sender_acc = await get_account_by_discord(session, interaction.user.id)
            if not sender_acc:
                return await interaction.followup.send("❌ You are not linked.")

            receiver_acc = await get_account_by_discord(session, target.id)
            if not receiver_acc:
                return await interaction.followup.send("❌ Target user is not linked.")

            sender_balance = float(sender_acc.get("balance", 0))
            if sender_balance < amount:
                return await interaction.followup.send(
                    f"❌ You don't have enough WeirdCoins. You have **{sender_balance:.2f}**."
                )

            receiver_balance = float(receiver_acc.get("balance", 0))

            await update_account_balance(
                session,
                int(sender_acc["discord_id"]),
                sender_balance - amount
            )
            await update_account_balance(
                session,
                int(receiver_acc["discord_id"]),
                receiver_balance + amount
            )

            await interaction.followup.send(
                f"✅ {interaction.user.mention} sent **{amount:.2f} WeirdCoins** to {target.mention}.\n"
                f"Your new balance: **{sender_balance - amount:.2f} WeirdCoins**."
            )

    except Exception as e:
        print("TRANSFER ERROR:", e)
        await interaction.followup.send("❌ Internal error.")

# ============================================================
# /blackjack AMOUNT — gamble WeirdCoins
# ============================================================
@tree.command(name="blackjack", description="Play blackjack with a WeirdCoins bet")
async def blackjack(interaction: discord.Interaction, amount: float):
    await interaction.response.defer(thinking=True)

    if amount <= 0:
        return await interaction.followup.send("❌ Bet amount must be positive.")

    try:
        async with aiohttp.ClientSession() as session:
            acc = await get_account_by_discord(session, interaction.user.id)
            if not acc:
                return await interaction.followup.send("❌ You are not linked.")

            balance = float(acc.get("balance", 0))
            if balance < amount:
                return await interaction.followup.send(
                    f"❌ You don't have enough WeirdCoins. You have **{balance:.2f}**."
                )

            # Deduct bet up front
            new_balance = balance - amount
            await update_account_balance(session, int(acc["discord_id"]), new_balance)

            # Deal initial hands
            player_cards = [draw_card(), draw_card()]
            dealer_cards = [draw_card(), draw_card()]

            player_total = hand_value(player_cards)
            dealer_total = hand_value(dealer_cards)

            # Simple strategy: player hits until 17 or more
            while player_total < 17:
                player_cards.append(draw_card())
                player_total = hand_value(player_cards)
                if player_total > 21:
                    break

            # Dealer hits until 17 or more (standard)
            if player_total <= 21:
                while dealer_total < 17:
                    dealer_cards.append(draw_card())
                    dealer_total = hand_value(dealer_cards)

            result = ""
            payout = 0.0

            if player_total > 21:
                result = "💥 You busted and lost your bet."
            elif dealer_total > 21:
                result = "🎉 Dealer busted, you win!"
                payout = amount * 2  # give double what they put in
            elif player_total > dealer_total:
                result = "🎉 You win!"
                payout = amount * 2
            elif player_total < dealer_total:
                result = "😢 Dealer wins, you lost your bet."
            else:
                result = "🤝 Push! You get your bet back."
                payout = amount  # refund

            if payout > 0:
                final_balance = new_balance + payout
                await update_account_balance(session, int(acc["discord_id"]), final_balance)
            else:
                final_balance = new_balance

            player_str = format_hand(player_cards)
            dealer_str = format_hand(dealer_cards)

            await interaction.followup.send(
                f"🃏 **Blackjack Results for {interaction.user.mention}**\n"
                f"**Bet:** {amount:.2f} WeirdCoins\n\n"
                f"**Your hand:** {player_str}\n"
                f"**Dealer's hand:** {dealer_str}\n\n"
                f"{result}\n"
                f"💰 Your new balance: **{final_balance:.2f} WeirdCoins**."
            )

    except Exception as e:
        print("BLACKJACK ERROR:", e)
        await interaction.followup.send("❌ Internal error during blackjack.")

# ============================================================
# STARTUP
# ============================================================
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online as {bot.user}")

bot.run(DISCORD_TOKEN)
