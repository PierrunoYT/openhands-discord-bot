import os
import time
import asyncio
import logging
from logging.handlers import RotatingFileHandler

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from context7_client import Context7Client
from openhands_client import OpenHandsClient

load_dotenv()

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

console = logging.StreamHandler()
console.setFormatter(logging.Formatter(LOG_FMT))
root_logger.addHandler(console)

file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "bot.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setFormatter(logging.Formatter(LOG_FMT))
root_logger.addHandler(file_handler)

log = logging.getLogger("bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set in .env")

CONTEXT7_API_KEY = os.getenv("CONTEXT7_API_KEY", "")
OPENHANDS_API_KEY = os.getenv("OPENHANDS_API_KEY", "")
OPENHANDS_BASE_URL = os.getenv("OPENHANDS_BASE_URL", "https://app.all-hands.dev/api")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")

DEFAULT_LIBRARY = "/websites/all-hands_dev"

LIBRARY_CHOICES = [
    app_commands.Choice(name="Official Docs (default)", value="/websites/all-hands_dev"),
    app_commands.Choice(name="GitHub Repo", value="/openhands/openhands"),
    app_commands.Choice(name="All sources", value="__all__"),
]

ALL_LIBRARY_IDS = [
    "/websites/all-hands_dev",
    "/openhands/openhands",
]

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
ctx7 = Context7Client(api_key=CONTEXT7_API_KEY)
openhands = None
if OPENHANDS_API_KEY:
    openhands = OpenHandsClient(api_key=OPENHANDS_API_KEY, base_url=OPENHANDS_BASE_URL)


@bot.event
async def on_ready():
    # Defensive: Bot.user or Bot.user.id could be None if not fully ready
    bot_user = getattr(bot, "user", None)
    bot_user_id = getattr(getattr(bot, "user", None), "id", None)
    log.info("Logged in as %s (ID: %s)", bot_user, bot_user_id)
    try:
        synced = await bot.tree.sync()
        log.info("Synced %d slash command(s)", len(synced))
    except Exception:
        log.exception("Failed to sync slash commands")


@bot.tree.command(name="ask", description="Ask a question about OpenHands")
@app_commands.describe(
    question="Your question about OpenHands",
    source="Which doc source to search (default: Official Docs)",
)
@app_commands.choices(source=LIBRARY_CHOICES)
async def ask_command(
    interaction: discord.Interaction,
    question: str,
    source: app_commands.Choice[str] | None = None,
):
    chosen = source.value if source else DEFAULT_LIBRARY
    lib_ids = ALL_LIBRARY_IDS if chosen == "__all__" else [chosen]
    source_label = source.name if source else "Official Docs"

    user = getattr(interaction, "user", None)
    guild = getattr(interaction, "guild", None)
    user_id = getattr(user, "id", None)
    guild_repr = guild if guild is not None else "DM"
    log.info(
        "/ask invoked by %s (%s) in %s — question: %r, source: %s",
        user, user_id, guild_repr, question, source_label,
    )
    try:
        await interaction.response.defer(thinking=True)
    except Exception:
        log.warning("Could not defer response to /ask", exc_info=True)
        # Fail gracefully and continue

    t0 = time.perf_counter()
    try:
        async def _fetch(lib_id: str) -> list[dict]:
            try:
                snippets = await ctx7.get_context(lib_id, question, response_type="json")
                if isinstance(snippets, list):
                    log.info("  %s returned %d snippet(s)", lib_id, len(snippets))
                    for s in snippets:
                        # Defensive: Don't overwrite user fields, but _source_lib is unique enough
                        s["_source_lib"] = lib_id
                    return snippets
                log.warning("  %s returned unexpected type: %s", lib_id, type(snippets).__name__)
            except Exception:
                log.warning("  Failed to fetch from %s, skipping", lib_id, exc_info=True)
            return []

        results = await asyncio.gather(*[_fetch(lib) for lib in lib_ids], return_exceptions=False)
        # Defensive: flatten results, skipping None
        all_snippets = [s for batch in results if isinstance(batch, list) for s in batch]

        elapsed = time.perf_counter() - t0

        if not all_snippets:
            log.info("No snippets found for %r (%.2fs)", question, elapsed)
            try:
                await interaction.followup.send(
                    "No documentation found for that question. Try rephrasing it."
                )
            except Exception:
                log.warning("Could not send followup for no snippets", exc_info=True)
            return

        log.info(
            "Fetched %d snippet(s) for %r (%.2fs)",
            len(all_snippets), question, elapsed,
        )
        try:
            embed = build_embed(question, all_snippets, source_label)
            await interaction.followup.send(embed=embed)
        except Exception as embed_exc:
            log.exception("Failed to send embed for %r: %r", question, embed_exc)
            await interaction.followup.send("Couldn't display results due to a formatting error.")

    except Exception as exc:
        log.exception("/ask failed for %r", question)
        try:
            await interaction.followup.send(f"Something went wrong: `{exc}`")
        except Exception:
            # Can't even send the error message
            pass


@bot.tree.command(name="openhands", description="Start an OpenHands task via Cloud API")
@app_commands.describe(
    task="The task description for OpenHands to execute",
    repository="Optional: GitHub repository (e.g., username/repo)",
)
async def openhands_command(
    interaction: discord.Interaction,
    task: str,
    repository: str | None = None,
):
    user = getattr(interaction, "user", None)
    guild = getattr(interaction, "guild", None)
    user_id = getattr(user, "id", None)
    guild_repr = guild if guild is not None else "DM"
    log.info(
        "/openhands invoked by %s (%s) in %s — task: %r, repo: %s",
        user, user_id, guild_repr, task, repository or "default",
    )

    if not openhands:
        log.warning("/openhands called but OPENHANDS_API_KEY not configured")
        try:
            await interaction.response.send_message(
                "❌ OpenHands API is not configured. Please set `OPENHANDS_API_KEY` in `.env`",
                ephemeral=True,
            )
        except Exception:
            log.warning("Could not send error response", exc_info=True)
        return

    repo = repository or GITHUB_REPO
    if not repo:
        try:
            await interaction.response.send_message(
                "❌ No repository specified. Either provide one or set `GITHUB_REPO` in `.env`",
                ephemeral=True,
            )
        except Exception:
            log.warning("Could not send error response", exc_info=True)
        return

    try:
        await interaction.response.defer(thinking=True)
    except Exception:
        log.warning("Could not defer response to /openhands", exc_info=True)

    try:
        t0 = time.perf_counter()
        result = await openhands.create_conversation(task=task, repository=repo)
        elapsed = time.perf_counter() - t0

        conv_id = result.get("conversation_id") or result.get("id") or "unknown"
        status = result.get("status", "unknown")
        
        log.info(
            "OpenHands conversation created: %s (status: %s, %.2fs)",
            conv_id, status, elapsed,
        )

        link = f"https://app.all-hands.dev/conversations/{conv_id}"

        embed = discord.Embed(
            title="✅ OpenHands Task Started",
            description=f"**Task:** {task}\n**Repository:** `{repo}`",
            color=0x57F287,
        )
        embed.add_field(name="Conversation ID", value=f"`{conv_id}`", inline=False)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="View Progress", value=f"[Open in OpenHands]({link})", inline=False)
        embed.set_footer(text="OpenHands Cloud API")

        await interaction.followup.send(embed=embed)

    except Exception as exc:
        log.exception("/openhands failed for task %r", task)
        try:
            error_msg = str(exc)
            if hasattr(exc, "message"):
                error_msg = exc.message[:500]
            await interaction.followup.send(
                f"❌ Failed to start OpenHands task:\n```\n{error_msg}\n```"
            )
        except Exception:
            pass


@bot.tree.command(name="openhands_status", description="Check the status of an OpenHands conversation")
@app_commands.describe(
    conversation_id="The conversation ID from /openhands",
)
async def openhands_status_command(
    interaction: discord.Interaction,
    conversation_id: str,
):
    user = getattr(interaction, "user", None)
    user_id = getattr(user, "id", None)
    log.info(
        "/openhands_status invoked by %s (%s) — conv_id: %s",
        user, user_id, conversation_id,
    )

    if not openhands:
        log.warning("/openhands_status called but OPENHANDS_API_KEY not configured")
        try:
            await interaction.response.send_message(
                "❌ OpenHands API is not configured. Please set `OPENHANDS_API_KEY` in `.env`",
                ephemeral=True,
            )
        except Exception:
            log.warning("Could not send error response", exc_info=True)
        return

    try:
        await interaction.response.defer(thinking=True)
    except Exception:
        log.warning("Could not defer response to /openhands_status", exc_info=True)

    try:
        t0 = time.perf_counter()
        result = await openhands.get_conversation_status(conversation_id)
        elapsed = time.perf_counter() - t0

        status = result.get("status", "unknown")
        log.info(
            "OpenHands conversation status: %s (status: %s, %.2fs)",
            conversation_id, status, elapsed,
        )

        link = f"https://app.all-hands.dev/conversations/{conversation_id}"

        embed = discord.Embed(
            title="OpenHands Conversation Status",
            description=f"**Conversation ID:** `{conversation_id}`",
            color=0x5865F2,
        )
        embed.add_field(name="Status", value=status, inline=True)
        
        if "created_at" in result:
            embed.add_field(name="Created", value=result["created_at"], inline=True)
        
        if "updated_at" in result:
            embed.add_field(name="Last Updated", value=result["updated_at"], inline=True)
        
        embed.add_field(name="View Details", value=f"[Open in OpenHands]({link})", inline=False)
        embed.set_footer(text="OpenHands Cloud API")

        await interaction.followup.send(embed=embed)

    except Exception as exc:
        log.exception("/openhands_status failed for conv_id %r", conversation_id)
        try:
            error_msg = str(exc)
            if hasattr(exc, "message"):
                error_msg = exc.message[:500]
            await interaction.followup.send(
                f"❌ Failed to get conversation status:\n```\n{error_msg}\n```"
            )
        except Exception:
            pass


@bot.tree.command(name="help_oh", description="Show what this bot can do")
async def help_command(interaction: discord.Interaction):
    user = getattr(interaction, "user", None)
    user_id = getattr(user, "id", None)
    log.info("/help_oh invoked by %s (%s)", user, user_id)
    
    commands_text = (
        "**Documentation Commands:**\n"
        "`/ask <question> [source]` — Ask anything about OpenHands\n"
        "`/help_oh` — Show this message\n"
    )
    
    if openhands:
        commands_text += (
            "\n**OpenHands Cloud API Commands:**\n"
            "`/openhands <task> [repository]` — Start an OpenHands task\n"
            "`/openhands_status <conversation_id>` — Check task status\n"
        )
    
    embed = discord.Embed(
        title="OpenHands Docs Bot",
        description=(
            "I answer questions about **OpenHands** using up-to-date documentation "
            "and can start tasks via the OpenHands Cloud API.\n\n"
            f"{commands_text}\n"
            "**Sources (optional dropdown):**\n"
            "• **Official Docs** — default, user-facing documentation\n"
            "• **GitHub Repo** — source code & dev docs\n"
            "• **All sources** — search everything\n\n"
            "**Example questions:**\n"
            "• How do I install OpenHands?\n"
            "• How to configure a custom agent?\n"
            "• What runtime sandbox options are available?\n"
            "• How does the event stream work?\n\n"
            "**Example tasks:**\n"
            "• `/openhands task:\"Add a README file\" repository:\"user/repo\"`\n"
            "• `/openhands task:\"Fix the login bug in auth.py\"`"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="Powered by Context7 & OpenHands Cloud API")
    try:
        await interaction.response.send_message(embed=embed)
    except Exception:
        log.warning("Could not send /help_oh message", exc_info=True)
        # Fail gracefully


def _dedup_snippets(snippets: list[dict]) -> list[dict]:
    """Remove near-duplicate snippets by comparing the first 200 chars of content."""
    seen: set[str] = set()
    unique: list[dict] = []
    for snip in snippets:
        content = snip.get("content", "")
        # Defensive: If content is not string, ignore this snippet
        if not isinstance(content, str):
            continue
        # Extract the first meaningful chunk as a fingerprint
        fingerprint = content[:200].strip().lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(snip)
    return unique


def _safe_truncate(text: str, limit: int) -> str:
    """Truncate text without breaking markdown code blocks."""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text

    truncated = text[: max(0, limit - 1)]

    # Count opening/closing fences to see if we're inside a code block
    fence_count = truncated.count("```")
    if fence_count % 2 != 0:
        # We're inside an unclosed code block — cut before it opened
        last_open = truncated.rfind("```")
        if last_open != -1:
            truncated = truncated[:last_open].rstrip()

    return truncated + "…"


def build_embed(query: str, snippets: list[dict], source_label: str = "Official Docs") -> discord.Embed:
    embed = discord.Embed(
        title="OpenHands",
        description=f"**Q:** {query}",
        color=0x57F287,
    )

    deduped = _dedup_snippets(snippets)
    log.info("Deduped %d → %d unique snippet(s)", len(snippets), len(deduped))

    total_len = 0
    max_embed_len = 5500
    max_field_len = 1024  # Discord hard limit per field

    for snip in deduped[:6]:
        title = snip.get("title", "Untitled")
        if not isinstance(title, str):
            title = str(title)
        content = snip.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        source = snip.get("source", "")
        if not isinstance(source, str):
            source = str(source)

        if source:
            source_link = f"\n[Source]({source})"
        else:
            source_link = ""

        available = max_field_len - len(source_link) - 1
        content = _safe_truncate(content, available)

        field_text = content + source_link

        if not field_text.strip():
            continue
        if total_len + len(field_text) > max_embed_len:
            break
        total_len += len(field_text)

        # Defensive: Discord field name max 256
        embed.add_field(name=title[:256], value=field_text, inline=False)

    embed.set_footer(text=f"Source: {source_label} · Powered by Context7")
    return embed


if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as run_exc:
        log.critical("Bot failed to run: %r", run_exc)
    finally:
        if openhands:
            asyncio.run(openhands.close())
        asyncio.run(ctx7.close())
