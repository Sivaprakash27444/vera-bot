"""
server.py — Vera bot HTTP server
Implements all 5 endpoints required by the magicpin judge harness.

Run: uvicorn server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from bot import compose

app = FastAPI(title="Vera Bot", version="1.0.0")
START = time.time()

# In-memory state
contexts: dict[tuple[str, str], dict] = {}   # (scope, context_id) -> {version, payload}
conversations: dict[str, list] = {}           # conversation_id -> [turns]
used_suppressions: set[str] = set()           # suppression keys already sent


def _get_ctx(scope: str, context_id: str) -> Optional[dict]:
    entry = contexts.get((scope, context_id))
    return entry["payload"] if entry else None


def _counts() -> dict:
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _) in contexts:
        counts[scope] = counts.get(scope, 0) + 1
    return counts


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START),
        "contexts_loaded": _counts(),
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Vera Challenge Submission",
        "team_members": ["Participant"],
        "model": "claude-sonnet-4-6",
        "approach": "Claude-powered composer with trigger-aware prompting, specificity anchoring, and Hindi-English code-mix support",
        "contact_email": "participant@example.com",
        "version": "1.0.0",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


@app.post("/v1/context")
async def push_context(body: CtxBody):
    valid_scopes = {"category", "merchant", "customer", "trigger"}
    if body.scope not in valid_scopes:
        return {"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {valid_scopes}"}

    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}

    contexts[key] = {"version": body.version, "payload": body.payload}
    ack_id = f"ack_{body.context_id}_v{body.version}_{uuid.uuid4().hex[:6]}"
    return {
        "accepted": True,
        "ack_id": ack_id,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }


class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []

    for trg_id in body.available_triggers:
        trg = _get_ctx("trigger", trg_id)
        if not trg:
            continue

        # Suppression check
        sup_key = trg.get("suppression_key", "")
        if sup_key and sup_key in used_suppressions:
            continue

        merchant_id = trg.get("merchant_id")
        if not merchant_id:
            continue

        merchant = _get_ctx("merchant", merchant_id)
        if not merchant:
            continue

        cat_slug = merchant.get("category_slug")
        category = _get_ctx("category", cat_slug) if cat_slug else None
        if not category:
            continue

        customer_id = trg.get("customer_id")
        customer = _get_ctx("customer", customer_id) if customer_id else None

        try:
            result = compose(category, merchant, trg, customer)
        except Exception as e:
            continue

        conv_id = f"conv_{merchant_id}_{trg_id}"
        # Don't reuse existing conversations in tick
        if conv_id in conversations:
            conv_id = f"{conv_id}_{uuid.uuid4().hex[:4]}"

        conversations[conv_id] = [{"from": "vera", "body": result["body"]}]
        if sup_key:
            used_suppressions.add(sup_key)

        action = {
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": result.get("send_as", "vera"),
            "trigger_id": trg_id,
            "template_name": f"vera_{trg.get('kind', 'generic')}_v1",
            "template_params": [
                merchant.get("identity", {}).get("name", ""),
                trg.get("kind", ""),
                result.get("body", "")[:50],
            ],
            "body": result["body"],
            "cta": result.get("cta", "open_ended"),
            "suppression_key": sup_key,
            "rationale": result.get("rationale", ""),
        }
        actions.append(action)

        # Cap at 20 actions per tick
        if len(actions) >= 20:
            break

    return {"actions": actions}


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


def _is_auto_reply(message: str, history: list) -> bool:
    """Detect WhatsApp Business auto-replies."""
    AUTO_PATTERNS = [
        "automated", "auto", "team tak pahuncha", "team se contact",
        "bahut-bahut shukriya", "aapki madad ke liye shukriya",
        "thank you for contacting", "we will get back to you",
        "our team will", "will be in touch", "out of office",
        "currently unavailable", "business hours",
    ]
    msg_lower = message.lower()
    
    # Pattern match
    for pattern in AUTO_PATTERNS:
        if pattern in msg_lower:
            return True
    
    # Same message repeated in history
    if history:
        prev_messages = [t.get("msg", "") for t in history if t.get("from") == "merchant"]
        if prev_messages.count(message) >= 2:
            return True
    
    return False


def _is_intent_to_act(message: str) -> bool:
    """Detect when merchant explicitly signals intent to proceed."""
    ACT_SIGNALS = [
        "yes", "haan", "ha", "ok", "okay", "sure", "let's do it",
        "go ahead", "please proceed", "karo", "kar do", "chalao",
        "i want to join", "judrna hai", "sign up", "subscribe",
        "confirm", "book it", "yes please",
    ]
    msg_lower = message.lower().strip()
    for signal in ACT_SIGNALS:
        if signal in msg_lower:
            return True
    return False


def _is_not_interested(message: str) -> bool:
    """Detect merchant signaling disinterest."""
    STOP_SIGNALS = [
        "stop", "nahi", "no", "not interested", "unsubscribe",
        "don't contact", "mat karo", "band karo", "remove me",
        "opt out", "leave me alone", "busy", "not now",
    ]
    msg_lower = message.lower().strip()
    for signal in STOP_SIGNALS:
        if signal in msg_lower:
            return True
    return False


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    history = conversations.get(body.conversation_id, [])
    history.append({"from": body.from_role, "msg": body.message})
    conversations[body.conversation_id] = history

    # Auto-reply detection
    if _is_auto_reply(body.message, history):
        # Try once more gently, then exit
        auto_count = sum(1 for t in history if t.get("from") == "merchant" and _is_auto_reply(t.get("msg", ""), []))
        if auto_count >= 2:
            return {
                "action": "end",
                "rationale": "Auto-reply detected repeatedly. Gracefully exiting to avoid spam; will retry at next scheduled cadence.",
            }
        return {
            "action": "send",
            "body": "Koi baat nahi — jab bhi time mile, seedha reply kar dena. Main yahan hoon! 🙂",
            "cta": "none",
            "rationale": "Detected auto-reply. Sent one soft acknowledgement before exit.",
        }

    # Not interested / stop signal
    if _is_not_interested(body.message):
        return {
            "action": "end",
            "rationale": "Merchant signaled not interested. Gracefully ending conversation.",
        }

    # Intent to act — pivot to action mode
    if _is_intent_to_act(body.message) and body.turn_number <= 2:
        merchant = None
        if body.merchant_id:
            merchant = _get_ctx("merchant", body.merchant_id)
        name = merchant.get("identity", {}).get("name", "") if merchant else ""
        offers = merchant.get("offers", []) if merchant else []
        active_offers = [o["title"] for o in offers if o.get("status") == "active"]
        offer_str = active_offers[0] if active_offers else "your top offer"
        
        return {
            "action": "send",
            "body": f"Bilkul! Main abhi {offer_str} ko activate kar rahi hoon aur aapka magicpin profile update karti hoon. Koi specific service ya timing preference hai? Reply karo.",
            "cta": "open_ended",
            "rationale": "Merchant expressed intent to proceed. Switched to action mode immediately (no more qualifying questions).",
        }

    # Generic contextual reply
    merchant = _get_ctx("merchant", body.merchant_id) if body.merchant_id else None
    if not merchant:
        return {
            "action": "send",
            "body": "Got it! Main isko note kar rahi hoon. Kuch aur chahiye? Reply karo.",
            "cta": "open_ended",
            "rationale": "Generic acknowledgement — merchant context not available.",
        }

    cat_slug = merchant.get("category_slug", "")
    category = _get_ctx("category", cat_slug) if cat_slug else {}
    
    # Build a contextual follow-up
    signals = merchant.get("signals", [])
    perf = merchant.get("performance", {})
    
    followup_prompts = []
    if "stale_posts" in str(signals):
        followup_prompts.append("Kya main aapke liye ek Google post draft kar sakti hoon? 2 min lagenge.")
    elif "ctr_below_peer" in str(signals):
        followup_prompts.append(f"Aapka CTR {perf.get('ctr', 0):.1%} hai — peer median se thoda kam. Ek updated post aur photo se 20-30% improvement ho sakta hai. Karein?")
    else:
        followup_prompts.append("Aur koi cheez hai jo main aapke liye update kar sakti hoon?")
    
    return {
        "action": "send",
        "body": followup_prompts[0],
        "cta": "yes_stop",
        "rationale": f"Turn {body.turn_number} follow-up based on merchant signals: {signals[:2]}. Offering next-best-action.",
    }
