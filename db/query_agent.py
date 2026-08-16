"""
Single-shot query agent: answers one ad-hoc question by calling
constrained tools (db/query_tools.py) rather than writing SQL itself.
This is the hybrid design — the LLM decides WHAT to look up and HOW to
phrase the answer; it never decides HOW to query the database, and every
tool validates its own arguments before touching CockroachDB (see
db/query_tools.py's docstring).

Sanitization: after the model produces its final answer, every dollar
figure mentioned in the answer text is checked against the raw tool
output. Any figure present in the prose but absent from what a tool
actually returned is flagged in `unverified_numbers` — a cheap, honest
guard against the model drifting a number between the tool result and
its narration, rather than trusting the prose blindly. This mirrors the
"code owns the numbers" discipline in db/reporting_agent.py, applied
per-question instead of per-full-report.

Deliberately NOT a multi-turn conversational agent — one question in, one
tool round, one answer out. A stateful chat loop is materially more
implementation risk (session handling, multi-round tool loops) for
comparatively little marginal value in a single-user demo; this covers
the "ask your financial memory a question" use case without it.
"""
import json
import os
import re

from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from db.query_tools import ALL_TOOLS

_TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

_llm = ChatBedrock(
    model_id=os.getenv("BEDROCK_MODEL_ID"),
    region_name=os.getenv("AWS_REGION"),
    model_kwargs={"temperature": 0},
).bind_tools(ALL_TOOLS)

SYSTEM_PROMPT = """You are a financial assistant for a small business owner, answering questions about their expenses and receipts.

You have three tools: query_category_spend, list_pending_matches, lookup_tax_rule. Use one or more of them to answer the question — never answer from memory or general assumptions about this business's actual numbers.

Rules:
- Every dollar figure and count in your answer must come directly from a tool result. Never estimate, round differently, or state a number no tool returned.
- If a tool returns an error (e.g. unknown category), either correct your arguments and retry with a tool the known_categories list suggests, or tell the user plainly what went wrong — do not silently make up an answer.
- Keep the final answer to 2-4 sentences, direct and specific, citing the actual figures.
"""

_MONEY_IN_ANSWER_RE = re.compile(r"\$\s?([\d,]+\.\d{2})")   # how money appears in the model's prose
_NUMBER_IN_TOOL_JSON_RE = re.compile(r"(\d+\.\d{2})")        # how it appears in raw tool JSON (no $ prefix)


def _extract_answer_figures(text: str) -> set[str]:
    return {m.replace(",", "") for m in _MONEY_IN_ANSWER_RE.findall(text)}


def _extract_known_figures(tool_json_blob: str) -> set[str]:
    return set(_NUMBER_IN_TOOL_JSON_RE.findall(tool_json_blob))


def ask(question: str) -> dict:
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=question)]
    tool_call_log = []

    response = _llm.invoke(messages)
    messages.append(response)

    for call in getattr(response, "tool_calls", None) or []:
        tool_fn = _TOOLS_BY_NAME.get(call["name"])
        result = tool_fn.invoke(call["args"]) if tool_fn else {"error": f"Unknown tool '{call['name']}'"}
        tool_call_log.append({"tool": call["name"], "args": call["args"], "result": result})
        messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"]))

    if tool_call_log:
        final = _llm.invoke(messages)
        answer_text = final.content
    else:
        # Model answered without calling anything — return what it said,
        # but the caller can see tool_calls is empty and treat it accordingly.
        answer_text = response.content

    tool_output_blob = json.dumps([c["result"] for c in tool_call_log], default=str)
    known_figures = _extract_known_figures(tool_output_blob)
    answer_figures = _extract_answer_figures(answer_text)
    unverified = sorted(answer_figures - known_figures)

    return {
        "answer": answer_text,
        "tool_calls": tool_call_log,
        "unverified_numbers": unverified,  # non-empty here means: treat this answer with suspicion
    }
