"""Точка входа: python -m app.bot"""
from __future__ import annotations

import logging

from dotenv import load_dotenv

from app.bot.client import build_bot, load_config


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    bot = build_bot(config)
    bot.run(config.token)


if __name__ == "__main__":
    main()
