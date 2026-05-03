"""
bot.py — Vera AI Challenge submission
Compose WhatsApp messages for merchants using Claude as the LLM backbone.
"""

from __future__ import annotations
import os
import json
import re
from typing import Optional

import anthropic

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

AUTO-REPLY DETECTION: If conversation shows same message repeated, or formal "team se pahunchaunga" type language, flag in rationale.
"""

def _build_prompt(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> str:
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
    
    instructions = f"""
Compose a WhatsApp message for:
- Merchant: {name} (owner: {owner})
- Trigger kind: {kind}
- Scope: {scope} ({"CUSTOMER-FACING — send_as=merchant_on_behalf" if customer else "MERCHANT-FACING — send_as=vera"})

Full context:
{json.dumps(ctx, indent=2, ensure_ascii=False)}

Return ONLY valid JSON. No markdown. No explanation outside JSON.
"""
    return instructions


def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> dict:
    """
    Compose a message given the 4 context layers.
    Returns dict with: body, cta, send_as, suppression_key, rationale
    """
    prompt = _build_prompt(category, merchant, trigger, customer)
    
    try:
        response = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip any markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        result = json.loads(text)
        # Ensure suppression_key falls back to trigger's
        if not result.get("suppression_key"):
            result["suppression_key"] = trigger.get("suppression_key", "")
        return result
    except Exception as e:
        # Fallback response
        merchant_name = merchant.get("identity", {}).get("name", "")
        return {
            "body": f"Namaste {merchant_name}! Vera here — quick update on your magicpin account. Reply YES to continue or STOP to opt out.",
            "cta": "yes_stop",
            "send_as": "vera",
            "suppression_key": trigger.get("suppression_key", "fallback"),
            "rationale": f"Fallback due to error: {str(e)[:100]}",
        }
