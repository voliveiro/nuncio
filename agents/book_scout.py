import anthropic
import datetime
import os
import time

BOOK_SCOUT_FILE = os.path.join(os.path.dirname(__file__), '..', 'logs', 'book_scout_last_run.txt')
BOOK_PREFERENCES_FILE = os.path.join(os.path.dirname(__file__), 'book_preferences.md')

client = anthropic.Anthropic()


def get_book_scout_status():
    if not os.path.exists(BOOK_SCOUT_FILE):
        return "never"
    with open(BOOK_SCOUT_FILE, 'r') as f:
        return f.read().strip()


def save_book_scout_timestamp():
    with open(BOOK_SCOUT_FILE, 'w') as f:
        f.write(datetime.datetime.now().strftime("%Y-%m-%d"))


def book_scout_prompt_fragment():
    last_run = get_book_scout_status()
    if last_run == "never":
        return "The book scout has never been run."
    try:
        last_date = datetime.datetime.strptime(last_run, "%Y-%m-%d")
        days_ago = (datetime.datetime.now() - last_date).days
        if days_ago >= 7:
            return f"The book scout was last run {days_ago} days ago (on {last_run}). This is more than a week — proactively ask Vernie at the start of this conversation if she would like you to run it now."
        else:
            return f"The book scout was last run {days_ago} days ago (on {last_run}). No need to prompt yet."
    except ValueError:
        return "The book scout has never been run."


def run_book_scout():
    if not os.path.exists(BOOK_PREFERENCES_FILE):
        return "Error: book_preferences.md not found."
    with open(BOOK_PREFERENCES_FILE, 'r') as f:
        preferences = f.read()

    today = datetime.datetime.now()
    cutoff = (today - datetime.timedelta(days=30)).strftime("%d %B %Y")
    month_terms = today.strftime("%B %Y")
    prev_month_terms = (today.replace(day=1) - datetime.timedelta(days=1)).strftime("%B %Y")

    scout_system = f"""You are a specialist book research agent. Your sole task is to find new and recently reviewed books matching the reading preferences provided.

Today's date is {today.strftime('%d %B %Y')}.
Recency cutoff: only include books with reviews or announcements published on or after {cutoff}. Skip anything older — if you cannot confirm the date, skip it.

You must search and report across exactly four categories. Search each category thoroughly before compiling results.

CATEGORY 1 — FICTION
Sources: Publishers Weekly, The Guardian Books, Literary Hub, Tor.com, Locus Magazine.
Includes: sci-fi, fantasy, literary fiction. Check for new releases by favourite authors first.
Append '{month_terms}' or '{prev_month_terms}' to queries to bias toward recent content.

CATEGORY 2 — RELIGION & SPIRITUALITY
Sources: Commonweal, America Magazine, Church Times, The Tablet, Publishers Weekly religion section.
Includes: Catholic and Orthodox theology, spirituality, contemplative writing, Jesuit tradition, ecumenism.
Authors to watch: James Martin SJ, Rowan Williams, and others in the preferences.

CATEGORY 3 — HISTORY
Sources: The Guardian Books, Literary Review, London Review of Books, Publishers Weekly.
Includes: Roman/Byzantine/medieval, 20th century (Soviet, Cold War, Southeast Asia), any period matching preferences.

CATEGORY 4 — CURRENT AFFAIRS
Sources: The Guardian, New Statesman, Foreign Affairs, The Atlantic, Prospect Magazine.
Includes: politics, democracy, technology and society, AI, geopolitics.

For each category, return a minimum of 3 and a maximum of 6 books. If you cannot find 3 confirmed books within the recency window for a category, include the most recent matches you found regardless of date, clearly noting they may be slightly older.

Output format — use exactly these section headers:

## Fiction

## Religion & Spirituality

## History

## Current Affairs

Within each section, format each entry as: **Title** — Author (Publisher, date) followed by 2–3 sentences on why it matches Vernie's taste.

CRITICAL RULES — failure to follow these will break the system:
- Output ONLY the formatted digest. Nothing before it, nothing after it.
- Do NOT narrate your search process or reasoning.
- Do NOT ask clarifying questions or request parameter adjustments. This is an automated report — there is no one to answer.
- Do NOT explain what you could not find. Just output the books you did find."""

    scout_messages = [{
        "role": "user",
        "content": [{"type": "text", "text": f"Find new book recommendations based on these preferences:\n\n{preferences}"}]
    }]
    scout_tools = [{"type": "web_search_20250305", "name": "web_search"}]

    print("[Book Scout agent starting...]")
    MAX_ITERATIONS = 30
    for _ in range(MAX_ITERATIONS):
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=8192,
                    system=scout_system,
                    tools=scout_tools,
                    messages=scout_messages
                )
                break
            except anthropic.APIStatusError as e:
                if e.status_code == 529 and attempt < 2:
                    time.sleep(15)
                else:
                    raise

        if response.stop_reason == "end_turn":
            digest = "".join(b.text for b in response.content if b.type == "text")
            save_book_scout_timestamp()
            print("[Book Scout agent complete.]")
            return digest

        elif response.stop_reason == "tool_use":
            # Web search is server-side: append the full response (including search
            # queries and results) as the assistant turn, then loop to let the model
            # continue generating its answer.
            scout_messages.append({"role": "assistant", "content": response.content})

        else:
            print(f"[Book Scout] unexpected stop_reason: {response.stop_reason}")
            break

    return "Book scout did not complete — please try again."
