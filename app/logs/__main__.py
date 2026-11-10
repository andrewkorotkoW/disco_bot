"""CLI индекса логов: `python -m app.logs ingest|player|vehicle|near|container`."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Optional

from .ingest import ingest
from .sqlite_source import SqliteLogSource


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _cmd_ingest(args: argparse.Namespace) -> int:
    stats = ingest(args.dir)
    print(
        f"файлов разобрано: {stats['files_parsed']}, "
        f"пропущено (не изменились): {stats['files_skipped']}, "
        f"событий записано: {stats['events']}"
    )
    return 0


def _cmd_player(args: argparse.Namespace) -> int:
    with SqliteLogSource() as source:
        identity = source.resolve(args.key)
        print(f"known names={sorted(identity.names)} steam64s={sorted(identity.steam64s)}")
        events = source.timeline(args.key, since=_parse_dt(args.since), until=_parse_dt(args.until))
        for event in events:
            print(f"{event.ts.isoformat()} [{event.kind}] {event.player or ''}: {event.text}")
        print(f"всего событий: {len(events)}")
    return 0


def _cmd_vehicle(args: argparse.Namespace) -> int:
    with SqliteLogSource() as source:
        events = source.vehicle_history(args.vehicle_class)
        for event in events:
            print(f"{event.ts.isoformat()} [{event.kind}] {event.player or ''}: {event.text}")
        print(f"всего событий: {len(events)}")
    return 0


def _cmd_container(args: argparse.Namespace) -> int:
    with SqliteLogSource() as source:
        events = source.container_access(
            args.container_class, since=_parse_dt(args.since), until=_parse_dt(args.until)
        )
        actors: set[str] = set()
        for event in events:
            actor = event.player or (event.extra or {}).get("actor_class") or "?"
            print(f"{event.ts.isoformat()} [{event.kind}] {actor}: {event.text}")
            actors.add(actor)
        print(f"всего событий: {len(events)}; брали/класли: {', '.join(sorted(actors)) or '—'}")
    return 0


def _cmd_near(args: argparse.Namespace) -> int:
    at = _parse_dt(args.at)
    if at is None:
        print("--at обязателен (ISO-время)", file=sys.stderr)
        return 2
    with SqliteLogSource() as source:
        results = source.nearby_players(args.x, args.z, args.radius, at, window_minutes=args.window)
        for r in results:
            print(f"{r['player']} ({r['steam64']}) — {r['distance']:.1f} м, {r['ts']}")
        print(f"всего игроков рядом: {len(results)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.logs")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="проиндексировать каталог с логами")
    p_ingest.add_argument("dir")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_player = sub.add_parser("player", help="таймлайн игрока по нику/steam64/BI-hash")
    p_player.add_argument("key")
    p_player.add_argument("--since")
    p_player.add_argument("--until")
    p_player.set_defaults(func=_cmd_player)

    p_vehicle = sub.add_parser("vehicle", help="история событий по классу машины")
    p_vehicle.add_argument("vehicle_class")
    p_vehicle.set_defaults(func=_cmd_vehicle)

    p_container = sub.add_parser("container", help="кто брал/класл в контейнер/машину заданного класса")
    p_container.add_argument("container_class")
    p_container.add_argument("--since")
    p_container.add_argument("--until")
    p_container.set_defaults(func=_cmd_container)

    p_near = sub.add_parser("near", help="кто был рядом с точкой во временном окне")
    p_near.add_argument("x", type=float)
    p_near.add_argument("z", type=float)
    p_near.add_argument("radius", type=float)
    p_near.add_argument("--at", required=True, help="ISO-время, например 2026-11-09T04:07:00")
    p_near.add_argument("--window", type=int, default=10, help="окно в минутах (по умолчанию 10)")
    p_near.set_defaults(func=_cmd_near)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
