import os
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from database import db, create_document, get_documents

app = FastAPI(title="Voting Demo Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- In-memory connections for WebSocket rooms (OK for demo) -----
class ConnectionManager:
    def __init__(self):
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, product_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active.setdefault(product_id, []).append(websocket)

    def disconnect(self, product_id: str, websocket: WebSocket):
        conns = self.active.get(product_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns and product_id in self.active:
            del self.active[product_id]

    async def broadcast(self, product_id: str, message: dict):
        for ws in list(self.active.get(product_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                # best-effort
                pass


manager = ConnectionManager()


# ----- Models -----
VoteOption = Literal["auction", "buy_now", "tokenization", "raffle", "not_interested"]


class ProductIn(BaseModel):
    title: str
    description: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    auction_start_price: Optional[float] = Field(None, ge=0)
    buy_now_price: Optional[float] = Field(None, ge=0)
    shares_total: Optional[int] = Field(None, ge=0)
    share_price: Optional[float] = Field(None, ge=0)
    raffle_tickets_total: Optional[int] = Field(None, ge=0)
    raffle_ticket_price: Optional[float] = Field(None, ge=0)
    vote_start_at: Optional[datetime] = None


class VoteIn(BaseModel):
    option: VoteOption
    desired_shares: Optional[int] = Field(None, ge=1)
    desired_tickets: Optional[int] = Field(None, ge=1)


# Utility to compute vote_end_at
DEFAULT_WINDOW_HOURS = int(os.getenv("VOTE_WINDOW_HOURS", "72"))


@app.get("/")
def read_root():
    return {"message": "Backend running", "now": datetime.now(timezone.utc).isoformat()}


@app.get("/test")
def test_database():
    resp = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            resp["database"] = "✅ Available"
            resp["connection_status"] = "Connected"
            resp["collections"] = db.list_collection_names()[:10]
            resp["database"] = "✅ Connected & Working"
    except Exception as e:
        resp["database"] = f"⚠️ Connected but error: {str(e)[:80]}"
    return resp


# ---- Demo Data Endpoints ----
@app.post("/api/admin/products")
def create_product(payload: ProductIn):
    now = datetime.now(timezone.utc)
    vote_start_at = payload.vote_start_at or now
    vote_end_at = vote_start_at + timedelta(hours=DEFAULT_WINDOW_HOURS)

    doc = {
        "locales": [
            {"locale": "ro", "title": payload.title, "description": payload.description},
            {"locale": "en", "title": payload.title, "description": payload.description},
            {"locale": "it", "title": payload.title, "description": payload.description},
        ],
        "images": payload.images,
        "auction_start_price": payload.auction_start_price,
        "buy_now_price": payload.buy_now_price,
        "shares_total": payload.shares_total,
        "share_price": payload.share_price,
        "raffle_tickets_total": payload.raffle_tickets_total,
        "raffle_ticket_price": payload.raffle_ticket_price,
        "vote_start_at": vote_start_at,
        "vote_end_at": vote_end_at,
        "status": "in_voting",
        "counts": {
            "auction": 0,
            "buy_now": 0,
            "tokenization": 0,
            "raffle": 0,
            "not_interested": 0,
        },
    }
    pid = create_document("product", doc)
    doc["_id"] = pid
    return {"data": doc}


@app.get("/api/products")
def list_products():
    items = get_documents("product", {"status": "in_voting"}, limit=100)
    # normalize id to string
    for it in items:
        it["_id"] = str(it.get("_id"))
    return {"data": items}


@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    from bson import ObjectId

    try:
        _id = ObjectId(product_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Product not found")
    items = get_documents("product", {"_id": _id}, limit=1)
    if not items:
        raise HTTPException(status_code=404, detail="Product not found")
    it = items[0]
    it["_id"] = str(it.get("_id"))
    return {"data": it}


@app.post("/api/products/{product_id}/vote")
def cast_vote(product_id: str, vote: VoteIn):
    from bson import ObjectId

    try:
        _id = ObjectId(product_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Product not found")

    items = get_documents("product", {"_id": _id}, limit=1)
    if not items:
        raise HTTPException(status_code=404, detail="Product not found")

    product = items[0]
    if product.get("status") != "in_voting":
        raise HTTPException(status_code=400, detail="Voting not active")

    # For demo: keep votes only as counters inside product doc
    counts = product.get("counts", {})
    if vote.option not in counts:
        raise HTTPException(status_code=400, detail="Invalid option")

    # naive uniqueness simulation using per-connection memory is not possible statelessly,
    # so we just increment; in production use user_id uniqueness.
    counts[vote.option] = int(counts.get(vote.option, 0)) + 1

    # persist update
    db["product"].update_one({"_id": _id}, {"$set": {"counts": counts}})

    # broadcast via WebSocket
    try:
        import anyio
        anyio.from_thread.run(manager.broadcast, product_id, {
            "type": "votes.update",
            "product_id": product_id,
            "counts": counts,
            "server_time": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass

    return {"data": {"ok": True, "counts": counts}}


@app.websocket("/ws/products/{product_id}")
async def ws_product(websocket: WebSocket, product_id: str):
    await manager.connect(product_id, websocket)
    try:
        while True:
            # we don't expect messages from clients; keepalive only
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(product_id, websocket)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
