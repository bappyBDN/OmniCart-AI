"""Per-customer conversation state shared by the web chat and Messenger.

One `conversation_states` row per customer holds the escalation status, the
Messenger order-form state, the active product and the last photo's visual
attributes. Every incoming message also gets a `message_sentiments` row.
"""
from datetime import datetime

from backend.records import ConversationState, MessageSentiment

ACTIVE = "ACTIVE"
ESCALATED = "ESCALATED"


def get_state(db, customer_id, shop_id, channel=None):
    """Load or create (unflushed) the state row; caller owns the transaction."""
    state = db.get(ConversationState, customer_id)
    if state is None:
        state = ConversationState(customer_id=customer_id, shop_id=shop_id, channel=channel or "web",
                                  status=ACTIVE, order_state={}, visual_context={},
                                  last_sentiment="NEUTRAL", last_sentiment_score=0.0, negative_streak=0)
        db.add(state)
    elif channel and state.channel != channel:
        state.channel = channel
    return state


def snapshot(db, customer_id):
    state = db.get(ConversationState, customer_id)
    if state is None:
        return {"status": ACTIVE, "order_state": {}, "visual_context": {}, "active_product_id": None,
                "last_sentiment": "NEUTRAL", "negative_streak": 0, "channel": "web"}
    return {"status": state.status, "order_state": dict(state.order_state or {}),
            "visual_context": dict(state.visual_context or {}), "active_product_id": state.active_product_id,
            "last_sentiment": state.last_sentiment, "negative_streak": state.negative_streak or 0,
            "channel": state.channel}


def record_sentiment(db, customer_id, shop_id, sentiment, channel="web", chat_message_id=None):
    """Log the classifier output and update the rolling negative streak.

    Returns the streak INCLUDING this message, for escalation decisions.
    """
    state = get_state(db, customer_id, shop_id, channel)
    streak = (state.negative_streak or 0) + 1 if sentiment.negative else 0
    state.negative_streak = streak
    state.last_sentiment, state.last_sentiment_score = sentiment.label, sentiment.score
    state.updated_at = datetime.utcnow()
    db.add(MessageSentiment(customer_id=customer_id, shop_id=shop_id, channel=channel, label=sentiment.label,
                            score=sentiment.score, urgency=sentiment.urgency, signals=list(sentiment.signals),
                            chat_message_id=chat_message_id))
    return streak


def set_status(db, customer_id, shop_id, status):
    state = get_state(db, customer_id, shop_id)
    state.status = status
    if status == ACTIVE:
        state.negative_streak = 0
    state.updated_at = datetime.utcnow()
    return state


def remember_turn(db, customer_id, shop_id, response, channel=None):
    """Persist cross-turn context the agent's ChatTurn metadata does not cover."""
    state = get_state(db, customer_id, shop_id, channel)
    if response.get("focus_product_id"):
        state.active_product_id = response["focus_product_id"]
    if response.get("visual_attributes"):
        state.visual_context = {**response["visual_attributes"],
                                "matched_product_ids": [s["data"]["product_id"] for s in response.get("sources", [])
                                                        if s.get("kind") == "product" and s.get("data")],
                                "at": datetime.utcnow().isoformat()}
    state.updated_at = datetime.utcnow()


def set_order_state(db, customer_id, shop_id, order_state):
    state = get_state(db, customer_id, shop_id)
    state.order_state = dict(order_state or {})
    state.updated_at = datetime.utcnow()
