from fastapi import APIRouter
from pydantic import BaseModel

from db.query_agent import ask

router = APIRouter(prefix="/agent", tags=["agent"])


class AskRequest(BaseModel):
    question: str


@router.post("/ask")
async def ask_agent(req: AskRequest):
    """Ask the financial memory agent a question — e.g. 'how much did I
    spend on meals last quarter' or 'what needs my review'. Answers by
    calling constrained tools (db/query_tools.py), never by writing SQL
    itself. Returns the answer, which tools were called with what args,
    and unverified_numbers — non-empty there means treat the answer with
    suspicion (see db/query_agent.py)."""
    return ask(req.question)
