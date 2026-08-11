from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import plaid
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from backend.plaid_client import client, PLAID_PRODUCTS, PLAID_COUNTRY_CODES
from backend import plaid_db

router = APIRouter(prefix="/plaid", tags=["plaid"])

# NOTE: single-user demo setup. In a real app, key everything off an
# authenticated user id instead of this constant.
DEMO_USER_ID = "demo-user"


class ExchangeRequest(BaseModel):
    public_token: str


@router.post("/create_link_token")
async def create_link_token():
    try:
        request = LinkTokenCreateRequest(
            products=PLAID_PRODUCTS,
            client_name="Hackathon Receipts + Transactions",
            country_codes=PLAID_COUNTRY_CODES,
            language="en",
            user=LinkTokenCreateRequestUser(client_user_id=DEMO_USER_ID),
        )
        response = client.link_token_create(request)
        return {"link_token": response.to_dict()["link_token"]}
    except plaid.ApiException as e:
        raise HTTPException(status_code=400, detail=e.body)


@router.post("/exchange_public_token")
async def exchange_public_token(req: ExchangeRequest):
    try:
        exchange_request = ItemPublicTokenExchangeRequest(public_token=req.public_token)
        response = client.item_public_token_exchange(exchange_request)
        access_token = response["access_token"]
        item_id = response["item_id"]

        plaid_db.save_item(item_id, access_token)

        # Do an initial sync right away so the frontend has data to show.
        _sync_item(item_id, access_token)

        return {"item_id": item_id}
    except plaid.ApiException as e:
        raise HTTPException(status_code=400, detail=e.body)


@router.post("/sync/{item_id}")
async def sync_transactions(item_id: str):
    item = plaid_db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Unknown item_id")
    try:
        added, modified, removed = _sync_item(item_id, item["access_token"], item.get("cursor"))
        return {"added": added, "modified": modified, "removed": removed}
    except plaid.ApiException as e:
        raise HTTPException(status_code=400, detail=e.body)


@router.get("/transactions")
async def list_transactions(item_id: str | None = None):
    return plaid_db.get_transactions(item_id)


@router.get("/items")
async def list_items():
    items = plaid_db.get_all_items()
    for i in items:
        i.pop("access_token", None)  # never expose access tokens to the frontend
    return items


def _sync_item(item_id: str, access_token: str, cursor: str | None = None):
    """Pulls all pages of /transactions/sync since the last stored cursor."""
    added_count = modified_count = removed_count = 0
    has_more = True
    while has_more:
        # The Plaid SDK rejects cursor=None outright — omit the kwarg
        # entirely on the first sync instead of passing None.
        if cursor:
            request = TransactionsSyncRequest(access_token=access_token, cursor=cursor)
        else:
            request = TransactionsSyncRequest(access_token=access_token)
        response = client.transactions_sync(request).to_dict()

        added = response.get("added", [])
        modified = response.get("modified", [])
        removed = response.get("removed", [])

        if added or modified:
            plaid_db.upsert_transactions(item_id, added + modified)
        if removed:
            plaid_db.remove_transactions([r["transaction_id"] for r in removed])

        added_count += len(added)
        modified_count += len(modified)
        removed_count += len(removed)

        cursor = response["next_cursor"]
        has_more = response["has_more"]

    plaid_db.update_cursor(item_id, cursor)
    return added_count, modified_count, removed_count