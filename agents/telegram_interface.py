"""
Nuncio Telegram Interface

Bidirectional Telegram bot for Nuncio. Only Vernie's Telegram user ID
(stored in keys/telegram_user_id.key) is allowed to send messages.

Run with:  python3 agents/telegram_interface.py

Shares conversation_history.json and memory.json with the terminal interface.
Messages are tagged [Via Telegram] in history so Nuncio knows the source.
"""

import asyncio
import datetime
import json
import os
import sys
import threading
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

# Import nuncio core (functions, tools, client, constants)
sys.path.insert(0, os.path.dirname(__file__))
import nuncio as n

# --- Config ---
BOT_TOKEN_FILE = os.path.join(os.path.dirname(__file__), '..', 'keys', 'telegram.key')
USER_ID_FILE = os.path.join(os.path.dirname(__file__), '..', 'keys', 'telegram_user_id.key')

with open(BOT_TOKEN_FILE) as f:
    BOT_TOKEN = f.read().strip()
with open(USER_ID_FILE) as f:
    ALLOWED_USER_ID = int(f.read().strip())

# --- Global state (set in main()) ---
_turn_lock: asyncio.Lock = None          # Serialise one Nuncio turn at a time
_bot_loop: asyncio.AbstractEventLoop = None
_bot_application: Application = None

# --- Confirmation bridge (sync executor thread ↔ async event loop) ---

_pending_lock = threading.Lock()
_pending_confirmation = None  # PendingConfirmation | None


class PendingConfirmation:
    def __init__(self):
        self.event = threading.Event()
        self.confirmed: bool = False


def request_confirmation_sync(tool_name: str, tool_input: dict, chat_id: int) -> bool:
    """
    Called from the sync Nuncio executor thread.
    Sends an inline-keyboard message to Telegram and blocks until Vernie
    taps Confirm or Cancel (or a 2-minute timeout, which denies).
    Returns True if confirmed, False otherwise.
    """
    global _pending_confirmation

    text = f"Nuncio wants to use: {tool_name}\n\n{json.dumps(tool_input, indent=2)}"
    if tool_name == "remember" and tool_input.get("source") == "external_url":
        text += "\n\nMemory derived from external content — verify before confirming."

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✓ Confirm", callback_data="confirm"),
        InlineKeyboardButton("✗ Cancel",  callback_data="cancel"),
    ]])

    pending = PendingConfirmation()
    with _pending_lock:
        _pending_confirmation = pending

    # Send the keyboard message from within the async loop
    send_future = asyncio.run_coroutine_threadsafe(
        _bot_application.bot.send_message(
            chat_id, text, reply_markup=keyboard
        ),
        _bot_loop,
    )
    try:
        send_future.result(timeout=10)
    except Exception as e:
        print(f"[Telegram] Failed to send confirmation message: {e}")
        with _pending_lock:
            _pending_confirmation = None
        return False

    # Block until Vernie taps a button (or timeout)
    responded = pending.event.wait(timeout=120)
    if not responded:
        with _pending_lock:
            _pending_confirmation = None
        asyncio.run_coroutine_threadsafe(
            _bot_application.bot.send_message(chat_id, "⏱ Confirmation timed out. Action cancelled."),
            _bot_loop,
        )
        return False

    return pending.confirmed


async def handle_callback(update: Update, context) -> None:
    """Handle Confirm / Cancel button presses."""
    global _pending_confirmation

    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        return  # Query too old (stale button from a previous session)

    if query.from_user.id != ALLOWED_USER_ID:
        return

    with _pending_lock:
        pending = _pending_confirmation
        _pending_confirmation = None

    if pending is None:
        try:
            await query.edit_message_text(query.message.text + "\n\n(No pending confirmation.)")
        except Exception:
            pass
        return

    pending.confirmed = (query.data == "confirm")
    pending.event.set()

    status = "✓ Confirmed" if pending.confirmed else "✗ Cancelled"
    try:
        await query.edit_message_text(query.message.text + f"\n\n{status}")
    except Exception:
        pass  # edit_message_text can fail if content is unchanged


# --- Nuncio conversation turn (sync, runs in executor thread) ---

def run_nuncio_turn(user_input: str, chat_id: int) -> str:
    """
    Runs one full Nuncio turn synchronously (designed for run_in_executor).
    Loads and saves shared conversation history on every call so state
    is consistent with terminal sessions.
    Returns Nuncio's text reply (empty string if response had no text).
    """
    conversation_history = n.load_history()
    memory_section = n.load_memory_for_prompt()
    book_scout_status = n.book_scout_prompt_fragment()

    system_prompt = f"""You are Nuncio, an AI agent with delegated authority to act on behalf of your principal, Vernie.
Today's date is {datetime.datetime.now().strftime("%A, %d %B %Y")}.
You are precise, loyal, and operate within clearly defined boundaries.
You are informed by Jesuit Catholic values.
You have access to Vernie's calendar, Gmail, Google Drive, and local filesystem.
You can search the web and fetch URLs to find current information, news, and research when Vernie asks about external topics.
Use your tools whenever a question requires real data.
Always tell Vernie what you found, not just that you looked.
When sending any email, always prefix the subject line with "[Nuncio] " and append the following line at the very bottom of the email body: "Email sent by Nuncio, Vernie's agent".
You have access to a local inbox folder at {n.NUNCIO_FOLDER}. Use the list_files tool to see what files are inside it. Only use read_file on specific files returned by list_files, never on folder paths.
This message came via Telegram. You can send Vernie proactive Telegram messages using the send_telegram_message tool.

## Book Scout
{book_scout_status}
When Vernie asks for a book search, or confirms she wants one, call the run_book_scout tool. It will spin up a specialist search agent that does all the research and returns a formatted digest — you do not need to search yourself. Present the digest to Vernie as-is when it arrives.

{memory_section}
"""

    conversation_history.append({
        "role": "user",
        "content": [{"type": "text", "text": f"[Via Telegram] {user_input}"}],
    })

    tool_call_counts: dict[str, int] = {}
    MAX_TOOL_RETRIES = 3
    SILENT_TOOLS = {"run_book_scout"}

    while True:
        # Call Claude with retries on overload
        for attempt in range(3):
            try:
                response = n.client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=system_prompt,
                    tools=n.tools,
                    messages=conversation_history,
                )
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(15)
                else:
                    raise

        if response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                result = None
                confirmation_status = "not_required"

                if tool_name in n.CONFIRMATION_REQUIRED:
                    confirmed = request_confirmation_sync(tool_name, tool_input, chat_id)
                    if not confirmed:
                        result = "Action cancelled by user."
                        confirmation_status = "denied"
                        n.append_action_log(tool_name, tool_input, result, confirmation_status)
                    else:
                        confirmation_status = "granted"

                if result is None:
                    tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1
                    if tool_call_counts[tool_name] > MAX_TOOL_RETRIES:
                        result = json.dumps({
                            "status": "error",
                            "reason": "retry_limit_reached",
                            "retryable": False,
                            "detail": f"{tool_name} called too many times. Report this failure to Vernie and do not retry.",
                        })
                        n.append_action_log(tool_name, tool_input, result, confirmation_status)
                    else:
                        if tool_name not in SILENT_TOOLS:
                            print(f"[Telegram][Tool: {tool_name}]")
                        result = n.execute_tool(tool_name, tool_input)
                        n.append_action_log(tool_name, tool_input, result, confirmation_status)

                MAX_LEN = 10000
                history_result = (
                    result[:MAX_LEN] + "\n[...truncated...]"
                    if isinstance(result, str) and len(result) > MAX_LEN
                    else result
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": history_result,
                })

            # Serialise assistant turn into history
            serialized = []
            for block in response.content:
                if block.type == "text" and block.text:
                    serialized.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    serialized.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            conversation_history.append({"role": "assistant", "content": serialized})
            conversation_history.append({"role": "user", "content": tool_results})

        else:
            # end_turn — extract reply, save, return
            reply = next((b.text for b in response.content if b.type == "text"), "")
            if reply:
                conversation_history.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": reply}],
                })
            n.save_history(conversation_history)
            return reply


# --- Telegram message handler ---

async def handle_message(update: Update, context) -> None:
    """Handle incoming text messages from Vernie."""
    if update.effective_user.id != ALLOWED_USER_ID:
        return  # Silently ignore anyone else

    chat_id = update.effective_chat.id
    user_text = update.message.text

    async with _turn_lock:
        await context.bot.send_chat_action(chat_id, "typing")
        loop = asyncio.get_running_loop()
        try:
            reply = await loop.run_in_executor(None, run_nuncio_turn, user_text, chat_id)
        except Exception as e:
            await context.bot.send_message(chat_id, f"⚠️ Nuncio encountered an error: {e}")
            return

    if not reply:
        reply = "[Nuncio completed the action with no text response]"

    # Telegram caps messages at 4096 characters
    for i in range(0, len(reply), 4096):
        await context.bot.send_message(chat_id, reply[i:i + 4096])


# --- Entry point ---

async def _post_init(application: Application) -> None:
    global _turn_lock, _bot_loop, _bot_application
    _turn_lock = asyncio.Lock()
    _bot_loop = asyncio.get_running_loop()
    _bot_application = application
    print(f"Nuncio Telegram interface starting. Listening for user ID {ALLOWED_USER_ID}...")


if __name__ == "__main__":
    application = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        print("\nNuncio Telegram interface stopped.")
