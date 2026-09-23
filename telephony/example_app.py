"""Runnable demo: uvicorn example_app:app --reload

Set TELEPHONY_TWILIO_ACCOUNT_SID / _AUTH_TOKEN / _FROM_NUMBER and point
TELEPHONY_PUBLIC_BASE_URL at an HTTPS tunnel (ngrok, cloudflared) so Twilio can
reach the webhooks -- the signature is checked against that exact URL.

    curl -X POST localhost:8000/telephony/calls \
      -H 'Idempotency-Key: demo-1' -H 'content-type: application/json' \
      -d '{"to": "+15550002222", "scenario": "support"}'
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException

from telephony import (
    CallContext,
    InMemoryLiveStateStore,
    LiveSessionRunner,
    OpenAILiveGateway,
    OpenAILiveTelephony,
    Telephony,
    ToolRegistry,
    build_runtime,
    create_telephony_router,
    get_settings,
    register_exception_handlers,
)
from telephony.twilio import TwilioProvider

ORDERS = {"1001": "shipped yesterday, arriving Friday", "1002": "still being packed"}
registry = ToolRegistry()


@registry.tool(
    "lookup_order",
    description="Look up the delivery status of an order by its order ID.",
    parameters={
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    },
    scenarios=["support"],
)
def lookup_order(order_id: str, *, context: CallContext) -> str:
    """Example application tool; S3 exposes this schema to the model."""

    status = ORDERS.get(order_id.strip())
    if status is None:
        return f"No order {order_id} for scenario {context.scenario}."
    return f"Order {order_id}: {status}."


def authenticate_api_user(x_api_key: str | None = Header(default=None)) -> None:
    """Stand-in for the host app's real auth. Webhooks do not use this."""

    if x_api_key != "change-me":
        raise HTTPException(status_code=401, detail="Bad X-API-Key")


settings = get_settings()
runtime = build_runtime(settings, registry)
live_state = InMemoryLiveStateStore()
live_calls = OpenAILiveTelephony(settings)
runner = LiveSessionRunner(
    gateway=OpenAILiveGateway(settings),
    runtime=runtime,
    live_state=live_state,
    registry=registry,
    # Lets the runner drop a wedged call's SIP leg at max_call_seconds.
    live_calls=live_calls,
    settings=settings,
)
telephony = Telephony(
    settings,
    provider=TwilioProvider(settings),
    live_calls=live_calls,
    agent=runtime,
    runner=runner,
    live_state=live_state,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await telephony.aclose()


app = FastAPI(title="telephony demo", lifespan=lifespan)
app.include_router(
    create_telephony_router(
        telephony=telephony,
        dependencies=[Depends(authenticate_api_user)],
    )
)
register_exception_handlers(app)
