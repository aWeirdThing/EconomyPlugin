import os
import discord
from discord import app_commands
import uuid
import firebase_admin
from firebase_admin import credentials, firestore
import asyncio
from concurrent.futures import ThreadPoolExecutor

# ---------------- LOAD ENV ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_PRIVATE_KEY = os.getenv("FIREBASE_PRIVATE_KEY").replace("\\n", "\n")
FIREBASE_CLIENT_EMAIL = os.getenv("FIREBASE_CLIENT_EMAIL")

# ---------------- FIREBASE INIT ----------------
cred = credentials.Certificate({
    "type": "service_account",
    "project_id": FIREBASE_PROJECT_ID,
    "private_key": FIREBASE_PRIVATE_KEY,
    "client_email": FIREBASE_CLIENT_EMAIL,
    "token_uri": "https://oauth2.googleapis.com/token"
})
firebase_admin.initialize_app(cred)
db = firestore.client()

# ---------------- DISCORD SETUP ----------------
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------------- THREAD POOL FOR FIRESTORE ----------------
executor = ThreadPoolExecutor(max_workers=20)

async def run_in_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, lambda: func(*args))

# ---------------- DATABASE FUNCTIONS ----------------
def get_user(discord_id):
    try:
        doc = db.collection("users").document(str(discord_id)).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        print(f"[ERROR] get_user({discord_id}): {e}")
        return None

def create_user(discord_id):
    try:
        uid = str(uuid.uuid4())
        db.collection("users").document(str(discord_id)).set({
            "uuid": uid,
            "discord_id": str(discord_id),
            "balance": 0
        })
        return uid
    except Exception as e:
        print(f"[ERROR] create_user({discord_id}): {e}")
        return None

def get_balance(discord_id):
    user = get_user(discord_id)
    if not user:
        create_user(discord_id)
        user = get_user(discord_id)
    return user["balance"] if user else 0

def update_balance(discord_id, amount):
    user = get_user(discord_id)
    if not user:
        create_user(discord_id)
        user = get_user(discord_id)
    if user:
        new_balance = user["balance"] + amount
        db.collection("users").document(str(discord_id)).update({"balance": new_balance})
        return new_balance
    return 0

# ---------------- ASYNC HELPERS ----------------
async def async_get_user(discord_id):
    return await run_in_executor(get_user, discord_id)

async def async_create_user(discord_id):
    return await run_in_executor(create_user, discord_id)

async def async_get_balance(discord_id):
    return await run_in_executor(get_balance, discord_id)

async def async_update_balance(discord_id, amount):
    return await run_in_executor(update_balance, discord_id, amount)

# ---------------- COMMANDS ----------------
@tree.command(name="link", description="Link your Discord account to the economy system")
async def link(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        user = await async_get_user(interaction.user.id)
        if user:
            await interaction.followup.send("🔗 Your account is already linked!", ephemeral=True)
            return
        await async_create_user(interaction.user.id)
        await interaction.followup.send("✅ Account linked successfully!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ Failed to link your account.", ephemeral=True)
        print(f"[ERROR] /link: {e}")

@tree.command(name="help", description="Show all economy commands")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    msg = (
        "**💰 Economy Commands**\n"
        "/link - Link your account  \n"
        "/balance - Check your balance  \n"
        "/sell item amount price - List item  \n"
        "/market - View listings  \n"
        "/buy listing_id - Buy listing  \n"
        "/give user amount - Give money  \n\n"
        "**👑 Admin Commands**\n"
        "/addmoney user amount  \n"
        "/removemoney user amount"
    )
    await interaction.followup.send(msg, ephemeral=True)

@tree.command(name="balance", description="Check your balance")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        bal = await async_get_balance(interaction.user.id)
        await interaction.followup.send(f"💰 Your balance: **{bal}**", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ Failed to fetch balance.", ephemeral=True)
        print(f"[ERROR] /balance: {e}")

@tree.command(name="give", description="Give money to another user")
async def give(interaction: discord.Interaction, user: discord.User, amount: float):
    await interaction.response.defer(ephemeral=True)
    try:
        if amount <= 0:
            return await interaction.followup.send("❌ Invalid amount.", ephemeral=True)
        sender_balance = await async_get_balance(interaction.user.id)
        if sender_balance < amount:
            return await interaction.followup.send("❌ Not enough money.", ephemeral=True)
        await async_update_balance(interaction.user.id, -amount)
        await async_update_balance(user.id, amount)
        await interaction.followup.send(f"✅ Gave {amount} to {user.mention}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ Failed to transfer money.", ephemeral=True)
        print(f"[ERROR] /give: {e}")

@tree.command(name="sell", description="List an item on the marketplace")
async def sell(interaction: discord.Interaction, item: str, amount: int, price: float):
    await interaction.response.defer(ephemeral=True)
    try:
        user = await async_get_user(interaction.user.id)
        if not user:
            await async_create_user(interaction.user.id)
            user = await async_get_user(interaction.user.id)
        await run_in_executor(lambda: db.collection("marketplace").add({
            "seller_uuid": user["uuid"],
            "item": item,
            "amount": amount,
            "price": price
        }))
        await interaction.followup.send(f"📦 Listed {amount}x {item} for {price} each.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ Failed to list item.", ephemeral=True)
        print(f"[ERROR] /sell: {e}")

@tree.command(name="market", description="View marketplace listings")
async def market(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        docs = await run_in_executor(lambda: list(db.collection("marketplace").limit(15).stream()))
        listings = [doc.to_dict() | {"id": doc.id} for doc in docs]
        if not listings:
            return await interaction.followup.send("📭 Marketplace is empty.", ephemeral=True)
        msg = "**🛒 Marketplace Listings**\n"
        for row in listings:
            msg += f"ID `{row['id']}` | {row['amount']}x {row['item']} | {row['price']} each\n"
        await interaction.followup.send(msg, ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ Failed to fetch marketplace.", ephemeral=True)
        print(f"[ERROR] /market: {e}")

@tree.command(name="buy", description="Buy a marketplace listing")
async def buy(interaction: discord.Interaction, listing_id: str):
    await interaction.response.defer(ephemeral=True)
    try:
        listing_ref = db.collection("marketplace").document(listing_id)
        listing_doc = await run_in_executor(listing_ref.get)
        if not listing_doc.exists:
            return await interaction.followup.send("❌ Listing not found.", ephemeral=True)
        listing = listing_doc.to_dict()
        total_price = listing["price"] * listing["amount"]
        buyer_balance = await async_get_balance(interaction.user.id)
        if buyer_balance < total_price:
            return await interaction.followup.send("❌ Not enough money.", ephemeral=True)
        buyer = await async_get_user(interaction.user.id)
        seller_docs = await run_in_executor(lambda: list(db.collection("users").where("uuid", "==", listing["seller_uuid"]).stream()))
        seller = next((s.to_dict() for s in seller_docs), None)
        if not seller:
            return await interaction.followup.send("❌ Seller not found.", ephemeral=True)
        await async_update_balance(interaction.user.id, -total_price)
        await async_update_balance(seller["discord_id"], total_price)
        await run_in_executor(lambda: db.collection("transactions").add({
            "buyer_uuid": buyer["uuid"],
            "seller_uuid": seller["uuid"],
            "item": listing["item"],
            "amount": listing["amount"],
            "price": listing["price"]
        }))
        await run_in_executor(listing_ref.delete)
        await interaction.followup.send(f"✅ Bought {listing['amount']}x {listing['item']} for {total_price}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ Failed to buy item.", ephemeral=True)
        print(f"[ERROR] /buy: {e}")

# ---------------- ADMIN ----------------
@tree.command(name="addmoney", description="Admin: Add money")
@app_commands.checks.has_permissions(administrator=True)
async def addmoney(interaction: discord.Interaction, user: discord.User, amount: float):
    await interaction.response.defer(ephemeral=True)
    try:
        await async_update_balance(user.id, amount)
        await interaction.followup.send(f"💸 Added {amount} to {user.mention}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ Failed to add money.", ephemeral=True)
        print(f"[ERROR] /addmoney: {e}")

@tree.command(name="removemoney", description="Admin: Remove money")
@app_commands.checks.has_permissions(administrator=True)
async def removemoney(interaction: discord.Interaction, user: discord.User, amount: float):
    await interaction.response.defer(ephemeral=True)
    try:
        await async_update_balance(user.id, -amount)
        await interaction.followup.send(f"💸 Removed {amount} from {user.mention}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ Failed to remove money.", ephemeral=True)
        print(f"[ERROR] /removemoney: {e}")

# ---------------- STARTUP ----------------
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online as {bot.user}")

bot.run(DISCORD_TOKEN)
