import os
import time
import uuid
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

# ---------------- LOAD ENV ----------------
load_dotenv()  # optional locally

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://daylvvjjuxxqrkreakwf.supabase.co/the")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # Must be service role key
API_KEY = os.getenv("API_KEY")  # Optional secret key for external calls
PORT = int(os.getenv("PORT", 8000))  # Railway sets $PORT automatically

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Supabase URL and KEY must be set in environment variables.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI(title="Minecraft-Discord Economy API")

# ---------------- MODELS ----------------
class LinkRequest(BaseModel):
    uuid: str

class GiveItemRequest(BaseModel):
    uuid: str
    item: str
    amount: int

class DeliverItemRequest(BaseModel):
    uuid: str
    item: str
    amount: int
    listing_id: int

# ---------------- AUTH ----------------
def verify_api_key(x_api_key: str = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

# ---------------- ROUTES ----------------

@app.post("/link", summary="Generate a link code for Minecraft account")
def link_account(data: LinkRequest, x_api_key: str = Header(None)):
    verify_api_key(x_api_key)
    code = str(uuid.uuid4())[:6]  # 6-character code
    supabase.table("link_codes").insert({
        "uuid": data.uuid,
        "code": code,
        "expires": int(time.time()) + 300  # 5 min expiry
    }).execute()
    return {"code": code, "expires_in": 300}

@app.get("/balance/{uuid}", summary="Get a user's balance")
def get_balance(uuid: str, x_api_key: str = Header(None)):
    verify_api_key(x_api_key)
    user = supabase.table("users").select("*").eq("uuid", uuid).execute().data
    if not user:
        return {"balance": 0}
    return {"balance": user[0]["balance"]}

@app.post("/give", summary="Queue an item to deliver to a user")
def give_item(data: GiveItemRequest, x_api_key: str = Header(None)):
    verify_api_key(x_api_key)
    supabase.table("pending_items").insert({
        "uuid": data.uuid,
        "item": data.item,
        "amount": data.amount
    }).execute()
    return {"status": "queued", "uuid": data.uuid, "item": data.item, "amount": data.amount}

@app.post("/deliver_item", summary="Deliver a purchased item from marketplace to user")
def deliver_item(data: DeliverItemRequest, x_api_key: str = Header(None)):
    """
    Used by the bot after a /buy command. Removes item from marketplace and adds to pending_items.
    """
    verify_api_key(x_api_key)
    # Check listing exists
    listing = supabase.table("marketplace").select("*").eq("id", data.listing_id).execute().data
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    listing = listing[0]
    # Remove listing from marketplace
    supabase.table("marketplace").delete().eq("id", data.listing_id).execute()

    # Queue item for delivery to Minecraft player
    supabase.table("pending_items").insert({
        "uuid": data.uuid,
        "item": data.item,
        "amount": data.amount
    }).execute()

    return {"status": "delivered", "uuid": data.uuid, "item": data.item, "amount": data.amount}

# Optional health check
@app.get("/health", summary="Health check endpoint")
def health():
    return {"status": "ok", "timestamp": int(time.time())}

# ---------------- RUN SERVER ----------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
