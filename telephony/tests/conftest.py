from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from telephony import (
    FakeProvider,
    Telephony,
    TelephonySettings,
    create_telephony_router,
    register_exception_handlers,
)
from telephony.twilio import TwilioProvider

AUTH_TOKEN = "test-auth-token"
BASE_URL = "http://testserver"


@pytest.fixture
def settings() -> TelephonySettings:
    return TelephonySettings(
        # Ignore the developer's own .env: otherwise real keys leak into the
        # fixtures and tests pass or fail depending on whose machine runs them.
        _env_file=None,
        twilio_auth_token=AUTH_TOKEN,
        twilio_account_sid="AC" + "0" * 32,
        twilio_from_number="+15550001111",
        openai_project_id="proj_test",
        public_base_url=BASE_URL,
        allowed_transfer_prefixes=["+1555", "+7700"],
        allowed_sip_domains=["support.example.com"],
        max_concurrent_calls=3,
        disclosure=None,
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def telephony(settings, provider) -> Telephony:
    tel = Telephony(settings, provider=provider)
    # Webhook verification and TwiML come from the real Twilio adapter; only
    # the outbound REST calls are faked.
    twilio = TwilioProvider(settings, client=object())
    provider.verify_webhook = twilio.verify_webhook  # type: ignore[method-assign]
    provider.answer_response = twilio.answer_response  # type: ignore[method-assign]
    provider.reject_response = TwilioProvider.reject_response  # type: ignore[attr-defined]
    return tel


@pytest.fixture
def client(telephony) -> TestClient:
    app = FastAPI()
    app.include_router(create_telephony_router(telephony=telephony))
    register_exception_handlers(app)
    return TestClient(app)


def sign(url: str, params: dict[str, str]) -> dict[str, str]:
    """Headers a genuine Twilio request would carry."""

    signature = RequestValidator(AUTH_TOKEN).compute_signature(url, params)
    return {
        "X-Twilio-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }
