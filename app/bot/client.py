"""Discord-слой: тонкая обвязка над app.bot.handlers.

Парсит discord-объекты (Message, Interaction) в dataclass'ы из handlers.py,
вызывает бизнес-логику оттуда и рендерит результат в discord.Embed. Бизнес-
логика сюда не просачивается.
"""
from __future__ import annotations

import logging
import os
import re
import textwrap
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from app.bot.handlers import (
    AnalysisResult,
    CaseDetail,
    SimilarCase,
    Ticket,
    analyze_ticket,
    get_case_detail,
    resolve_ticket_case,
    search_similar,
)
from app.cases import CaseStore
from app.logs import LogSource, NullLogSource

logger = logging.getLogger("app.bot")

_MESSAGE_LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")


@dataclass
class BotConfig:
    token: str
    guild_id: Optional[int]
    ticket_channel_ids: set[int]
    admin_channel_id: int


def _parse_channel_ids(raw: Optional[str]) -> set[int]:
    if not raw:
        return set()
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


def load_config() -> BotConfig:
    """Читает конфиг бота из переменных окружения (см. .env.example)."""
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN не задан (см. .env.example)")

    admin_channel_raw = os.environ.get("DISCORD_ADMIN_CHANNEL_ID")
    if not admin_channel_raw:
        raise RuntimeError("DISCORD_ADMIN_CHANNEL_ID не задан (см. .env.example)")

    guild_raw = os.environ.get("DISCORD_GUILD_ID")
    return BotConfig(
        token=token,
        guild_id=int(guild_raw) if guild_raw else None,
        ticket_channel_ids=_parse_channel_ids(os.environ.get("DISCORD_TICKET_CHANNEL_IDS")),
        admin_channel_id=int(admin_channel_raw),
    )


def _ticket_from_message(message: discord.Message) -> Ticket:
    return Ticket(
        text=message.content,
        source_channel_id=message.channel.id,
        source_message_id=message.id,
        player_name=str(message.author),
        player_id=message.author.id,
    )


def _jump_url(guild_id: Optional[int], channel_id: Optional[int], message_id: Optional[int]) -> Optional[str]:
    if channel_id is None or message_id is None or guild_id is None:
        return None
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _render_similar_list(similar: list[SimilarCase], guild_id: Optional[int]) -> str:
    lines = []
    for case, decision, score, method in similar:
        url = _jump_url(guild_id, case.source_channel_id, case.source_message_id)
        link = f"[ссылка]({url})" if url else "без ссылки"
        summary = textwrap.shorten(case.summary or case.ticket_text, width=100, placeholder="…")
        if decision:
            decision_text = textwrap.shorten(decision.decision_text, width=150, placeholder="…")
        else:
            decision_text = "не решён"
        lines.append(f"**#{case.id}** ({score:.2f}, {method}) — {summary} — {link} — решение: {decision_text}")
    return "\n".join(lines)


def render_analysis_embed(
    result: AnalysisResult, *, source_url: Optional[str], guild_id: Optional[int]
) -> discord.Embed:
    extraction = result.extraction
    advice = result.advice

    embed = discord.Embed(
        title=f"Кейс #{result.case.id} — {extraction.category}",
        description=advice.summary,
        color=discord.Color.blurple(),
    )
    if source_url:
        embed.add_field(name="Источник", value=source_url, inline=False)

    facts_text = "\n".join(f"• {fact}" for fact in advice.facts) if advice.facts else "Логи не подключены."
    embed.add_field(name="Факты", value=facts_text, inline=False)

    similar_text = _render_similar_list(result.similar, guild_id) or "Похожих кейсов не найдено."
    embed.add_field(name="Похожие кейсы", value=similar_text, inline=False)

    embed.add_field(name="Рекомендация", value=advice.recommendation, inline=False)
    embed.add_field(name="Уверенность", value=f"{advice.confidence:.0%}", inline=True)
    return embed


def render_similar_embed(
    query_text: str, results: list[SimilarCase], guild_id: Optional[int]
) -> discord.Embed:
    embed = discord.Embed(
        title="Похожие кейсы",
        description=textwrap.shorten(query_text, width=200, placeholder="…"),
        color=discord.Color.green(),
    )
    value = _render_similar_list(results, guild_id) if results else "Похожих кейсов не найдено."
    embed.add_field(name="Результаты", value=value, inline=False)
    return embed


def render_case_embed(detail: CaseDetail, guild_id: Optional[int]) -> discord.Embed:
    case = detail.case
    embed = discord.Embed(
        title=f"Кейс #{case.id} [{case.status}]",
        description=textwrap.shorten(case.summary or case.ticket_text, width=200, placeholder="…"),
        color=discord.Color.orange() if case.status == "open" else discord.Color.dark_green(),
    )
    embed.add_field(
        name="Текст тикета",
        value=textwrap.shorten(case.ticket_text, width=500, placeholder="…"),
        inline=False,
    )
    embed.add_field(name="Игрок", value=case.player_name or "неизвестно", inline=True)
    embed.add_field(name="Категория", value=case.category or "не определена", inline=True)

    url = _jump_url(guild_id, case.source_channel_id, case.source_message_id)
    if url:
        embed.add_field(name="Источник", value=url, inline=False)

    if detail.decision:
        embed.add_field(name="Решение", value=detail.decision.decision_text, inline=False)
    else:
        embed.add_field(name="Решение", value="кейс пока не решён", inline=False)
    return embed


class DiscoBot(commands.Bot):
    def __init__(
        self,
        *,
        ticket_channel_ids: set[int],
        admin_channel_id: int,
        guild_id: Optional[int] = None,
        store: Optional[CaseStore] = None,
        log_source: Optional[LogSource] = None,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.ticket_channel_ids = ticket_channel_ids
        self.admin_channel_id = admin_channel_id
        self.guild_id = guild_id
        self.store = store or CaseStore()
        self.log_source = log_source or NullLogSource()

    async def setup_hook(self) -> None:
        await self.add_cog(BotCommands(self))
        if self.guild_id is not None:
            guild_obj = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s", self.user)

    def _is_ticket_channel(self, channel: discord.abc.Messageable) -> bool:
        if isinstance(channel, discord.Thread):
            return channel.parent_id in self.ticket_channel_ids
        return getattr(channel, "id", None) in self.ticket_channel_ids

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.content:
            return
        if not self._is_ticket_channel(message.channel):
            return

        ticket = _ticket_from_message(message)
        result = analyze_ticket(self.store, ticket, log_source=self.log_source)
        await self._post_analysis(result, source_url=message.jump_url)

    async def _get_case_thread(self, case_id: int, label: str) -> discord.Thread:
        channel = self.get_channel(self.admin_channel_id) or await self.fetch_channel(self.admin_channel_id)
        if isinstance(channel, discord.Thread):
            return channel
        anchor = await channel.send(f"Новый разбор: кейс #{case_id} — {label}")
        return await anchor.create_thread(name=f"Кейс #{case_id}")

    async def _post_analysis(self, result: AnalysisResult, *, source_url: Optional[str]) -> None:
        thread = await self._get_case_thread(result.case.id, source_url or result.advice.summary)
        embed = render_analysis_embed(result, source_url=source_url, guild_id=self.guild_id)
        await thread.send(embed=embed)


class BotCommands(commands.Cog):
    def __init__(self, bot: DiscoBot):
        self.bot = bot

    async def _resolve_message(self, interaction: discord.Interaction, message_ref: str) -> discord.Message:
        match = _MESSAGE_LINK_RE.search(message_ref)
        if match:
            _, channel_id, message_id = (int(part) for part in match.groups())
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            return await channel.fetch_message(message_id)

        message_id = int(message_ref.strip())
        candidate_channels = [interaction.channel] + [
            self.bot.get_channel(channel_id) for channel_id in self.bot.ticket_channel_ids
        ]
        for channel in candidate_channels:
            if channel is None:
                continue
            try:
                return await channel.fetch_message(message_id)
            except discord.NotFound:
                continue
        raise ValueError(f"сообщение {message_ref} не найдено ни в одном из известных каналов")

    @app_commands.command(name="analyze", description="Разбор тикета по ссылке на сообщение или его ID")
    @app_commands.describe(message="Ссылка на сообщение Discord или его ID")
    async def analyze(self, interaction: discord.Interaction, message: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            source = await self._resolve_message(interaction, message)
        except (ValueError, discord.NotFound, discord.Forbidden) as exc:
            await interaction.followup.send(f"Не удалось найти сообщение: {exc}")
            return

        ticket = _ticket_from_message(source)
        result = analyze_ticket(self.bot.store, ticket, log_source=self.bot.log_source)
        thread = await self.bot._get_case_thread(result.case.id, source.jump_url)
        embed = render_analysis_embed(result, source_url=source.jump_url, guild_id=self.bot.guild_id)
        await thread.send(embed=embed)
        await interaction.followup.send(f"Разбор готов: {thread.mention} (кейс #{result.case.id})")

    @app_commands.command(name="similar", description="Поиск похожих кейсов по тексту (без вызова Claude)")
    @app_commands.describe(text="Текст запроса", k="Сколько кейсов показать (по умолчанию 5)")
    async def similar(self, interaction: discord.Interaction, text: str, k: int = 5) -> None:
        await interaction.response.defer(thinking=True)
        results = search_similar(self.bot.store, text, k=k)
        embed = render_similar_embed(text, results, guild_id=self.bot.guild_id)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="resolve", description="Зафиксировать решение админа по кейсу")
    @app_commands.describe(case_id="ID кейса", decision="Текст решения")
    async def resolve(self, interaction: discord.Interaction, case_id: int, decision: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            result = resolve_ticket_case(self.bot.store, case_id, interaction.user.id, decision)
        except ValueError as exc:
            await interaction.followup.send(str(exc))
            return
        await interaction.followup.send(f"Кейс #{case_id} решён: {result.decision_text}")

    @app_commands.command(name="case", description="Карточка кейса по ID")
    @app_commands.describe(case_id="ID кейса")
    async def case(self, interaction: discord.Interaction, case_id: int) -> None:
        await interaction.response.defer(thinking=True)
        detail = get_case_detail(self.bot.store, case_id)
        if detail is None:
            await interaction.followup.send(f"Кейс #{case_id} не найден")
            return
        embed = render_case_embed(detail, guild_id=self.bot.guild_id)
        await interaction.followup.send(embed=embed)


def build_bot(
    config: Optional[BotConfig] = None,
    *,
    store: Optional[CaseStore] = None,
    log_source: Optional[LogSource] = None,
) -> DiscoBot:
    config = config or load_config()
    return DiscoBot(
        ticket_channel_ids=config.ticket_channel_ids,
        admin_channel_id=config.admin_channel_id,
        guild_id=config.guild_id,
        store=store,
        log_source=log_source,
    )
