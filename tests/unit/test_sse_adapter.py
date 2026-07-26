import pytest
from datetime import datetime
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.genai import types

from app.api.sse_adapter import SSEEnvelopeAdapter, classify_tool_name


def test_classify_tool_name():
    assert classify_tool_name("save_user_profile") == "state"
    assert classify_tool_name("search_medical_products") == "query"
    assert classify_tool_name("unknown_tool") == "tool"


def test_adapter_initialization():
    adapter = SSEEnvelopeAdapter(prompt="hello", initial_state={"key": "val"})
    assert adapter.prompt == "hello"
    assert adapter.merged_state == {"key": "val"}
    assert adapter.sequence == 0
    assert len(adapter.seen_texts) == 0


def test_build_meta_envelope():
    adapter = SSEEnvelopeAdapter(prompt="hello")
    meta = adapter.build_meta_envelope()
    assert meta["type"] == "meta"
    assert "transport" in meta


def test_build_done_envelope():
    adapter = SSEEnvelopeAdapter(prompt="hello")
    done = adapter.build_done_envelope("final text", {"user:age": "30"})
    assert done["type"] == "done"
    assert done["finalText"] == "final text"
    assert done["state"] == {"user:age": "30"}


def test_build_error_envelope():
    adapter = SSEEnvelopeAdapter(prompt="hello")
    err = adapter.build_error_envelope("An error occurred", "INTERNAL_ERROR")
    assert err["type"] == "error"
    assert err["message"] == "An error occurred"
    assert err["error_code"] == "INTERNAL_ERROR"


def test_is_echoed_user_input():
    adapter = SSEEnvelopeAdapter(prompt="hello")

    # Mock user event matching prompt
    user_event = Event(
        author="user",
        content=types.Content(parts=[types.Part(text="hello")]),
    )
    assert adapter.is_echoed_user_input(user_event) is True

    # Mock user event not matching prompt
    other_event = Event(
        author="user",
        content=types.Content(parts=[types.Part(text="world")]),
    )
    assert adapter.is_echoed_user_input(other_event) is False

    # Mock agent event
    agent_event = Event(
        author="agent",
        content=types.Content(parts=[types.Part(text="hello")]),
    )
    assert adapter.is_echoed_user_input(agent_event) is False


def test_map_adk_event_to_envelopes_text():
    adapter = SSEEnvelopeAdapter(prompt="hello")

    agent_event = Event(
        author="agent",
        content=types.Content(parts=[types.Part(text="This is a test response.")]),
    )

    envelopes = adapter.map_adk_event_to_envelopes(agent_event)

    # Should contain timeline event and message event
    assert len(envelopes) == 2
    assert any(e["type"] == "timeline" for e in envelopes)
    assert any(e["type"] == "message" for e in envelopes)

    msg_env = next(e for e in envelopes if e["type"] == "message")
    assert msg_env["text"] == "This is a test response."
    assert msg_env["mode"] == "replace"


def test_map_adk_event_to_envelopes_state():
    adapter = SSEEnvelopeAdapter(prompt="hello")

    state_event = Event(
        author="agent",
        content=types.Content(parts=[]),
        actions=EventActions(state_delta={"user:age": 35}),
    )

    envelopes = adapter.map_adk_event_to_envelopes(state_event)

    assert len(envelopes) == 2
    assert any(e["type"] == "timeline" for e in envelopes)
    assert any(e["type"] == "state" for e in envelopes)

    state_env = next(e for e in envelopes if e["type"] == "state")
    assert state_env["patch"] == {"user:age": "35"}
    assert adapter.merged_state["user:age"] == "35"
