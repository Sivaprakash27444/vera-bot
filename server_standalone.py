"""
server_standalone.py — Vera bot HTTP server (ALL-IN-ONE)
All 5 required endpoints + bot logic in one file.
No external imports except fastapi, uvicorn, anthropic.

Run: uvicorn server_standalone:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations
import os, time, uuid, json, re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel
import anthropic

# ─── Bot core ─────────────────────────────────────────────────────────────────

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """You are Vera, magicpin's merchant AI assistant. You compose WhatsApp messages to help merchants grow their business.

RULES (never break these):
1. Output ONLY valid JSON with keys: body, cta, send_as, suppression_key, rationale
2. body: the WhatsApp message text. Concise, warm, specific. No preambles like "I hope you're well".
3. cta: one of "yes_stop", "open_ended", "none"
   - "yes_stop" for action triggers (recall, appointment, milestone, renewal)
   - "open_ended" for info/curiosity triggers
   - "none" for pure info (regulatory, digest)
4. send_as: "vera" for merchant-facing, "merchant_on_behalf" for customer-facing (when customer context provided)
5. suppression_key: copy from trigger's suppression_key field
6. rationale: 1-2 sentences explaining why this message, what lever it uses

MESSAGE QUALITY RULES:
- Anchor on a specific verifiable fact (number, date, source, stat) from context
- Never invent data not in the context
- Service+price format beats generic discounts ("Haircut @ ₹99" not "10% off")
- Hindi-English code-mix when merchant languages include "hi"
- Match voice to category: dentists = peer/clinical, salons = warm/stylish, restaurants = casual/local, gyms = energetic, pharmacies = helpful/clinical
- End with single CTA in the last sentence
- No multi-choice CTAs except for booking flows (slots)
- No promotional tone ("AMAZING DEAL!")
- No re-introduction after first message
- For customer-facing: use merchant name as sender context, be personal to the customer
- Use compulsion levers: specificity, loss aversion, social proof, effort externalization, curiosity, reciprocity
- Keep it under 160 words for merchant messages, under 120 for customer messages
"""

def _compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> dict:
    ctx = {
        "category": {
            "slug": category.get("slug"),
            "voice": category.get("voice", {}),
            "offer_catalog": category.get("offer_catalog", [])[:5],
            "peer_stats": category.get("peer_stats", {}),
            "digest": category.get("digest", [])[:3],
            "seasonal_beats": category.get("seasonal_beats", []),
            "trend_signals": category.get("trend_signals", [])[:2],
        },
        "merchant": {
            "merchant_id": merchant.get("merchant_id"),
            "identity": merchant.get("identity", {}),
            "subscription": merchant.get("subscription", {}),
            "performance": merchant.get("performance", {}),
            "offers": merchant.get("offers", []),
            "conversation_history": merchant.get("conversation_history", [])[-3:],
            "customer_aggregate": merchant.get("customer_aggregate", {}),
            "signals": merchant.get("signals", []),
            "review_themes": merchant.get("review_themes", []),
        },
        "trigger": trigger,
    }
    if customer:
        ctx["customer"] = customer

    name = merchant.get("identity", {}).get("name", "")
    owner = merchant.get("identity", {}).get("owner_first_name", "")
    kind = trigger.get("kind", "")
    scope = trigger.get("scope", "merchant")

    prompt = f"""Compose a WhatsApp message for:
- Merchant: {name} (owner: {owner})
- Trigger kind: {kind}
- Scope: {scope} ({"CUSTOMER-FACING — send_as=merchant_on_behalf" if customer else "MERCHANT-FACING — send_as=vera"})

Full context:
{json.dumps(ctx, indent=2, ensure_ascii=False)}

Return ONLY valid JSON. No markdown. No explanation outside JSON."""

    try:
        response = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        result = json.loads(text)
        if not result.get("suppression_key"):
            result["suppression_key"] = trigger.get("suppression_key", "")
        return result
    except Exception as e:
        merchant_name = merchant.get("identity", {}).get("name", "")
        return {
            "body": f"Namaste {merchant_name}! Vera here — quick update on your magicpin account. Reply YES to continue or STOP to opt out.",
            "cta": "yes_stop",
            "send_as": "vera",
            "suppression_key": trigger.get("suppression_key", "fallback"),
            "rationale": f"Fallback due to error: {str(e)[:100]}",
        }

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Vera Bot", version="1.0.0")
START = time.time()

contexts: dict[tuple[str, str], dict] = {}
conversations: dict[str, list] = {}
used_suppressions: set[str] = set()


def _get_ctx(scope: str, context_id: str) -> Optional[dict]:
    entry = contexts.get((scope, context_id))
    return entry["payload"] if entry else None


def _counts() -> dict:
    counts: dict = {}
    for (scope, _) in contexts:
        counts[scope] = counts.get(scope, 0) + 1
    return counts


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
        "model": "claude-sonnet-4-20250514",
        "approach": "Claude-powered composer with trigger-aware prompting, specificity anchoring, and Hindi-English code-mix support",
        "contact_email": "vera@magicpin.com",
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
            result = _compose(category, merchant, trg, customer)
        except Exception:
            continue

        conv_id = f"conv_{merchant_id}_{trg_id}"
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
    AUTO_PATTERNS = [
        "automated", "auto", "team tak pahuncha", "team se contact",
        "bahut-bahut shukriya", "aapki madad ke liye shukriya",
        "thank you for contacting", "we will get back to you",
        "our team will", "will be in touch", "out of office",
        "currently unavailable", "business hours",
    ]
    msg_lower = message.lower()
    for pattern in AUTO_PATTERNS:
        if pattern in msg_lower:
            return True
    if history:
        prev_messages = [t.get("msg", "") for t in history if t.get("from") == "merchant"]
        if prev_messages.count(message) >= 2:
            return True
    return False


def _is_intent_to_act(message: str) -> bool:
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

    if _is_auto_reply(body.message, history):
        auto_count = sum(1 for t in history if t.get("from") == "merchant" and _is_auto_reply(t.get("msg", ""), []))
        if auto_count >= 2:
            return {
                "action": "end",
                "rationale": "Auto-reply detected repeatedly. Gracefully exiting.",
            }
        return {
            "action": "send",
            "body": "Koi baat nahi — jab bhi time mile, seedha reply kar dena. Main yahan hoon! 🙂",
            "cta": "none",
            "rationale": "Detected auto-reply. Sent one soft acknowledgement before exit.",
        }

    if _is_not_interested(body.message):
        return {
            "action": "end",
            "rationale": "Merchant signaled not interested. Gracefully ending conversation.",
        }

    if _is_intent_to_act(body.message) and body.turn_number <= 2:
        merchant = _get_ctx("merchant", body.merchant_id) if body.merchant_id else None
        name = merchant.get("identity", {}).get("name", "") if merchant else ""
        offers = merchant.get("offers", []) if merchant else []
        active_offers = [o["title"] for o in offers if o.get("status") == "active"]
        offer_str = active_offers[0] if active_offers else "your top offer"
        return {
            "action": "send",
            "body": f"Bilkul! Main abhi {offer_str} ko activate kar rahi hoon aur aapka magicpin profile update karti hoon. Koi specific service ya timing preference hai? Reply karo.",
            "cta": "open_ended",
            "rationale": "Merchant expressed intent to proceed. Switched to action mode.",
        }

    merchant = _get_ctx("merchant", body.merchant_id) if body.merchant_id else None
    if not merchant:
        return {
            "action": "send",
            "body": "Got it! Main isko note kar rahi hoon. Kuch aur chahiye? Reply karo.",
            "cta": "open_ended",
            "rationale": "Generic acknowledgement — merchant context not available.",
        }

    signals = merchant.get("signals", [])
    perf = merchant.get("performance", {})

    if "stale_posts" in str(signals):
        followup = "Kya main aapke liye ek Google post draft kar sakti hoon? 2 min lagenge."
    elif "ctr_below_peer" in str(signals):
        followup = f"Aapka CTR {perf.get('ctr', 0):.1%} hai — peer median se thoda kam. Ek updated post aur photo se 20-30% improvement ho sakta hai. Karein?"
    else:
        followup = "Aur koi cheez hai jo main aapke liye update kar sakti hoon?"

    return {
        "action": "send",
        "body": followup,
        "cta": "yes_stop",
        "rationale": f"Turn {body.turn_number} follow-up based on merchant signals.",
    }
