"""
Unit Tests - Zero-UI Identity and Entitlement System [INV-AUTH]

Covers:
  - EntitlementTier enum values
  - UserIdentity lifecycle: TRIAL active, TRIAL expired, LIFETIME, EXPIRED
  - ClientHello identity field propagation (to_dict / from_dict round-trip)
  - Server AUTH_ERROR gate: expired identity rejected before session is armed
  - Server AUTH pass: active identity allowed; BYOK api_key forwarded to orchestrator
  - days_remaining boundary calculations
"""

import asyncio
import json
import time
import pytest

from src.zero_ui.contracts import (
    EntitlementTier,
    UserIdentity,
    ClientHello,
    ClientMode,
    ClientCapabilities,
    _TRIAL_DURATION_SEC,
)
from src.zero_ui.server import CentralZeroUIServer


# Helpers

def _make_identity(email="user@example.com", tier=EntitlementTier.TRIAL, elapsed_sec=0, api_key=None):
    return UserIdentity(
        email=email,
        tier=tier,
        trial_start_epoch_sec=int(time.time()) - elapsed_sec,
        api_key=api_key,
    )


# EntitlementTier enum

class TestEntitlementTierEnum:
    def test_tier_values(self):
        assert EntitlementTier.TRIAL.value == "TRIAL"
        assert EntitlementTier.LIFETIME.value == "LIFETIME"
        assert EntitlementTier.EXPIRED.value == "EXPIRED"

    def test_tier_is_str_subclass(self):
        assert isinstance(EntitlementTier.TRIAL, str)


# UserIdentity is_active()

class TestUserIdentityIsActive:
    def test_trial_active_at_day_zero(self):
        assert _make_identity(tier=EntitlementTier.TRIAL, elapsed_sec=0).is_active() is True

    def test_trial_active_just_before_expiry(self):
        assert _make_identity(tier=EntitlementTier.TRIAL, elapsed_sec=_TRIAL_DURATION_SEC - 1).is_active() is True

    def test_trial_expired_at_boundary(self):
        assert _make_identity(tier=EntitlementTier.TRIAL, elapsed_sec=_TRIAL_DURATION_SEC).is_active() is False

    def test_trial_expired_long_past(self):
        assert _make_identity(tier=EntitlementTier.TRIAL, elapsed_sec=_TRIAL_DURATION_SEC + 86400).is_active() is False

    def test_lifetime_always_active(self):
        assert _make_identity(tier=EntitlementTier.LIFETIME, elapsed_sec=_TRIAL_DURATION_SEC + 999_999).is_active() is True

    def test_expired_tier_never_active(self):
        assert _make_identity(tier=EntitlementTier.EXPIRED, elapsed_sec=0).is_active() is False


# UserIdentity days_remaining()

class TestUserIdentityDaysRemaining:
    def test_fresh_trial_30_days(self):
        assert _make_identity(tier=EntitlementTier.TRIAL, elapsed_sec=0).days_remaining() == 30

    def test_trial_at_day_15(self):
        assert _make_identity(tier=EntitlementTier.TRIAL, elapsed_sec=15 * 24 * 3600).days_remaining() == 15

    def test_trial_expired_returns_zero(self):
        assert _make_identity(tier=EntitlementTier.TRIAL, elapsed_sec=_TRIAL_DURATION_SEC + 86400).days_remaining() == 0

    def test_lifetime_returns_zero(self):
        assert _make_identity(tier=EntitlementTier.LIFETIME).days_remaining() == 0

    def test_expired_tier_returns_minus_one(self):
        assert _make_identity(tier=EntitlementTier.EXPIRED).days_remaining() == -1


# UserIdentity serialization round-trip

class TestUserIdentitySerialization:
    def test_to_dict_contains_required_keys(self):
        identity = _make_identity(tier=EntitlementTier.LIFETIME, api_key="my-api-key")
        d = identity.to_dict()
        assert d["email"] == "user@example.com"
        assert d["tier"] == "LIFETIME"
        assert d["api_key"] == "my-api-key"
        assert "trial_start_epoch_sec" in d

    def test_from_dict_round_trip(self):
        identity = _make_identity(tier=EntitlementTier.TRIAL, api_key="byok-key")
        restored = UserIdentity.from_dict(identity.to_dict())
        assert restored.email == identity.email
        assert restored.tier == EntitlementTier.TRIAL
        assert restored.api_key == "byok-key"
        assert restored.trial_start_epoch_sec == identity.trial_start_epoch_sec

    def test_to_json_from_json_round_trip(self):
        identity = _make_identity(tier=EntitlementTier.EXPIRED)
        restored = UserIdentity.from_json(identity.to_json())
        assert restored.tier == EntitlementTier.EXPIRED
        assert restored.is_active() is False


# ClientHello identity field propagation

class TestClientHelloIdentity:
    def _make_hello(self, identity=None):
        return ClientHello(
            client_id="test_client",
            client_mode=ClientMode.PC_STATION,
            capabilities=ClientCapabilities(camera_pdaf=False),
            identity=identity,
        )

    def test_hello_without_identity_roundtrip(self):
        hello = self._make_hello(identity=None)
        restored = ClientHello.from_dict(hello.to_dict())
        assert restored.identity is None

    def test_hello_with_trial_identity_roundtrip(self):
        identity = _make_identity(tier=EntitlementTier.TRIAL)
        hello = self._make_hello(identity=identity)
        d = hello.to_dict()
        assert d["identity"]["tier"] == "TRIAL"
        restored = ClientHello.from_dict(d)
        assert restored.identity is not None
        assert restored.identity.tier == EntitlementTier.TRIAL

    def test_hello_with_lifetime_identity_roundtrip(self):
        identity = _make_identity(tier=EntitlementTier.LIFETIME, api_key="my-byok-key")
        hello = self._make_hello(identity=identity)
        restored = ClientHello.from_dict(hello.to_dict())
        assert restored.identity.tier == EntitlementTier.LIFETIME
        assert restored.identity.api_key == "my-byok-key"

    def test_hello_json_round_trip_with_identity(self):
        identity = _make_identity(tier=EntitlementTier.TRIAL)
        hello = self._make_hello(identity=identity)
        restored = ClientHello.from_json(hello.to_json())
        assert restored.identity.tier == EntitlementTier.TRIAL


# Server AUTH gate (end-to-end over local WebSocket)

class TestServerEntitlementGate:

    def _build_hello_dict(self, client_id, tier=EntitlementTier.TRIAL, elapsed_sec=0, api_key=None):
        identity = _make_identity(
            email=f"{client_id}@test.com",
            tier=tier,
            elapsed_sec=elapsed_sec,
            api_key=api_key,
        )
        return ClientHello(client_id=client_id, client_mode=ClientMode.PC_STATION, identity=identity).to_dict()

    def test_expired_trial_rejected_with_auth_error(self):
        import websockets
        async def _run():
            server = CentralZeroUIServer(port=18781)
            server_task = asyncio.create_task(server.start())
            await asyncio.sleep(0.05)
            try:
                async with websockets.connect("ws://127.0.0.1:18781") as ws:
                    expired_hello = self._build_hello_dict("expired_client", tier=EntitlementTier.TRIAL, elapsed_sec=_TRIAL_DURATION_SEC + 1)
                    await ws.send(json.dumps(expired_hello))
                    resp = json.loads(await ws.recv())
                    assert resp["type"] == "AUTH_ERROR", f"Expected AUTH_ERROR, got: {resp}"
                    assert resp["code"] == "ENTITLEMENT_EXPIRED"
                    assert "sess_expired_client" not in server.active_sessions
            finally:
                await server.stop()
                server_task.cancel()
                try: await server_task
                except: pass
        asyncio.run(_run())

    def test_explicit_expired_tier_rejected(self):
        import websockets
        async def _run():
            server = CentralZeroUIServer(port=18782)
            server_task = asyncio.create_task(server.start())
            await asyncio.sleep(0.05)
            try:
                async with websockets.connect("ws://127.0.0.1:18782") as ws:
                    hello_dict = self._build_hello_dict("explicit_expired", tier=EntitlementTier.EXPIRED)
                    await ws.send(json.dumps(hello_dict))
                    resp = json.loads(await ws.recv())
                    assert resp["type"] == "AUTH_ERROR"
                    assert "sess_explicit_expired" not in server.active_sessions
            finally:
                await server.stop()
                server_task.cancel()
                try: await server_task
                except: pass
        asyncio.run(_run())

    def test_active_trial_allowed_and_session_armed(self):
        import websockets
        async def _run():
            server = CentralZeroUIServer(port=18783)
            server_task = asyncio.create_task(server.start())
            await asyncio.sleep(0.05)
            try:
                async with websockets.connect("ws://127.0.0.1:18783") as ws:
                    hello_dict = self._build_hello_dict("active_trial_client", tier=EntitlementTier.TRIAL)
                    await ws.send(json.dumps(hello_dict))
                    resp = json.loads(await ws.recv())
                    assert resp["type"] == "SERVER_READY", f"Expected SERVER_READY, got: {resp}"
                    assert resp["status"] == "ARMED"
                    assert "sess_active_trial_client" in server.active_sessions
            finally:
                await server.stop()
                server_task.cancel()
                try: await server_task
                except: pass
        asyncio.run(_run())

    def test_lifetime_identity_always_allowed(self):
        import websockets
        async def _run():
            server = CentralZeroUIServer(port=18784)
            server_task = asyncio.create_task(server.start())
            await asyncio.sleep(0.05)
            try:
                async with websockets.connect("ws://127.0.0.1:18784") as ws:
                    hello_dict = self._build_hello_dict("lifetime_client", tier=EntitlementTier.LIFETIME, elapsed_sec=_TRIAL_DURATION_SEC * 10)
                    await ws.send(json.dumps(hello_dict))
                    resp = json.loads(await ws.recv())
                    assert resp["type"] == "SERVER_READY"
                    assert resp["status"] == "ARMED"
            finally:
                await server.stop()
                server_task.cancel()
                try: await server_task
                except: pass
        asyncio.run(_run())

    def test_byok_api_key_forwarded_to_orchestrator(self):
        import websockets
        async def _run():
            server = CentralZeroUIServer(port=18785)
            server_task = asyncio.create_task(server.start())
            await asyncio.sleep(0.05)
            try:
                async with websockets.connect("ws://127.0.0.1:18785") as ws:
                    hello_dict = self._build_hello_dict("byok_client", tier=EntitlementTier.LIFETIME, api_key="user-byok-key-abc123")
                    await ws.send(json.dumps(hello_dict))
                    await ws.recv()
                    orch = server.active_sessions.get("sess_byok_client")
                    assert orch is not None
                    assert orch.session_context.get("byok_api_key") == "user-byok-key-abc123"
                    assert orch.session_context.get("entitlement_tier") == "LIFETIME"
                    assert orch.session_context.get("user_email") == "byok_client@test.com"
            finally:
                await server.stop()
                server_task.cancel()
                try: await server_task
                except: pass
        asyncio.run(_run())

    def test_no_identity_still_connects(self):
        import websockets
        async def _run():
            server = CentralZeroUIServer(port=18786)
            server_task = asyncio.create_task(server.start())
            await asyncio.sleep(0.05)
            try:
                async with websockets.connect("ws://127.0.0.1:18786") as ws:
                    hello = ClientHello(client_id="anon_client", client_mode=ClientMode.PC_STATION, identity=None)
                    await ws.send(hello.to_json())
                    resp = json.loads(await ws.recv())
                    assert resp["type"] == "SERVER_READY"
            finally:
                await server.stop()
                server_task.cancel()
                try: await server_task
                except: pass
        asyncio.run(_run())
