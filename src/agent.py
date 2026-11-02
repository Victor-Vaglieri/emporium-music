# LangGraph ReAct agent factory and conversation session runner.

import logging
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from src.config import get_llm_settings
from src.database import get_db_connection
from src.prompts import get_system_prompt
from src.tools import ALL_TOOLS

logger = logging.getLogger(__name__)

# Allow up to ~6 sequential tool-call rounds per turn (each round uses 2 graph steps: agent + tools).
DEFAULT_RECURSION_LIMIT = 15


# Instantiate the configured chat model (Google Gemini or Groq) with validated parameters.
def create_chat_model(
    provider: str | None = None, temperature: float = 0.2
) -> BaseChatModel:
    temp = float(temperature)
    if temp < 0.0 or temp > 2.0:
        raise ValueError(f"Temperature must be between 0.0 and 2.0, got: {temperature}")

    settings = get_llm_settings(provider_override=provider)
    selected_provider = settings["provider"]

    if selected_provider == "gemini":
        return ChatGoogleGenerativeAI(
            model=settings["model"],
            google_api_key=settings["api_key"],
            temperature=temp,
        )
    if selected_provider == "groq":
        return ChatGroq(
            model=settings["model"],
            api_key=settings["api_key"],
            temperature=temp,
        )
    raise ValueError(f"Unsupported LLM provider: '{selected_provider}'.")


# Normalize LangChain message content (string or list of content blocks) into plain text.
def extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(p.strip() for p in parts if p and p.strip()).strip()
    return ""


# Extract the text from the last assistant (AI) message in a LangGraph message list.
def extract_last_ai_reply(messages: list[Any]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage) or getattr(msg, "type", None) == "ai":
            # Skip intermediate AI messages that only contain tool calls without final text.
            text = extract_message_text(getattr(msg, "content", None))
            if text:
                return text
    return ""


# Build the compiled LangGraph ReAct agent with tools, persona prompt, and in-memory state.
def build_agent(
    provider: str | None = None,
    reference_date: str | None = None,
    checkpointer: Any | None = None,
) -> Any:
    get_db_connection()
    llm = create_chat_model(provider=provider)
    memory = checkpointer if checkpointer is not None else MemorySaver()
    system_prompt = get_system_prompt(reference_date=reference_date)
    return create_react_agent(
        model=llm,
        tools=ALL_TOOLS,
        prompt=system_prompt,
        checkpointer=memory,
    )


# Send a customer message to the agent within a thread session and return the assistant reply.
def chat_with_agent(
    agent: Any,
    user_message: str,
    thread_id: str = "default-session",
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> str:
    cleaned_msg = (user_message or "").strip()
    if not cleaned_msg:
        return ""

    cleaned_thread = (thread_id or "").strip()
    if not cleaned_thread:
        raise ValueError("A non-empty 'thread_id' is required for session isolation.")

    if int(recursion_limit) < 4:
        raise ValueError("'recursion_limit' must be at least 4 to complete a ReAct cycle.")

    config = {
        "configurable": {"thread_id": cleaned_thread},
        "recursion_limit": int(recursion_limit),
    }

    try:
        response = agent.invoke(
            {"messages": [("user", cleaned_msg)]},
            config=config,
        )
    except Exception as exc:
        logger.error(
            "Agent execution failed for thread '%s': %s",
            cleaned_thread,
            type(exc).__name__,
        )
        raise RuntimeError(
            "The assistant could not complete the request due to a model or execution error."
        ) from exc

    messages = response.get("messages", []) if isinstance(response, dict) else []
    return extract_last_ai_reply(messages)


# Engineering review notes (documented for README.md reference):
# 1. Resolved during refactoring:
#    - Added explicit provider and temperature validation in create_chat_model().
#    - Implemented extract_last_ai_reply() to scan backwards for the final AIMessage with text,
#      preventing intermediate ToolMessages or unsupported objects from being returned as replies.
#    - Added input validation for empty user messages, empty thread_ids, and configured
#      DEFAULT_RECURSION_LIMIT = 15 so legitimate multi-tool workflows succeed while runaway loops stop.
#    - Added safe error logging and RuntimeError wrapping in chat_with_agent() without leaking keys.
# 2. Known trade-offs left out to avoid over-engineering for this challenge:
#    - Conversation state uses LangGraph's in-memory MemorySaver() rather than an external SQLite
#      or Postgres checkpointer, matching the single-process CLI architecture documented in plano.md.
#    - Execution uses synchronous agent.invoke() because the Rich CLI is synchronous.
