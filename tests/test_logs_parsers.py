"""Тесты парсеров пяти форматов логов DayZ на фикстурах tests/fixtures/logs/
(см. docs/LOGS.md). Не трогает tests/test_logs.py (LogEvent/NullLogSource)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.logs import carcodelock, console, lbmaster, zf
from app.logs import adm as adm_mod

FIXTURES = Path(__file__).parent / "fixtures" / "logs"


# --------------------------------------------------------------------------
# 1. adm.py
# --------------------------------------------------------------------------

def _adm_events():
    summary = adm_mod.ParseSummary()
    events = list(adm_mod.parse_file(FIXTURES / "sample.ADM", summary=summary))
    return events, summary


def test_adm_connect_event():
    events, _ = _adm_events()
    connect = next(e for e in events if e.kind == "connect")
    assert connect.player == "Kazah"
    assert connect.bi_hash == "XWbcLXGVWt0G+XNh2Z0Tor+ONhXjzVy6Gqg/wBGhePc="
    assert connect.x == 4619.9
    assert connect.z == 8300.5
    assert connect.ts == datetime(2026, 11, 9, 23, 58, 2)


def test_adm_connecting_line_produces_no_event():
    events, _ = _adm_events()
    assert not any(e.kind == "connect" and e.ts.second == 1 for e in events)
    assert all("is connecting" not in (e.text or "") for e in events)


def test_adm_hit_infected():
    events, _ = _adm_events()
    hit = next(e for e in events if e.kind == "hit" and e.player == "Multik")
    assert hit.group == "22"
    assert hit.faction == "Zalupa"
    assert hit.hp == 85.7
    assert hit.x == 4700.1
    assert hit.z == 8290.2
    assert hit.extra["subtype"] == "infected"
    assert hit.attacker == "Зараженный"
    assert hit.body_part == "Torso"
    assert hit.damage == 6.175
    assert hit.weapon == "MeleeInfected"


def test_adm_hit_by_player_is_pvp_on_pve_server():
    events, _ = _adm_events()
    hit = next(e for e in events if e.kind == "hit" and e.player == "XVictim")
    assert hit.group is None
    assert hit.faction is None
    assert hit.extra["subtype"] == "player"
    assert hit.attacker == "YAttacker"
    assert hit.body_part == "LeftArm"
    assert hit.damage == 12.5
    assert hit.weapon == "Mlock-91"


def test_adm_hit_fall_damage():
    events, _ = _adm_events()
    hit = next(e for e in events if e.kind == "hit" and e.player == "X2Fall")
    assert hit.extra["subtype"] == "fall"
    assert hit.attacker == "FallDamageHealth"
    assert hit.weapon is None


def test_adm_hit_fire_damage():
    events, _ = _adm_events()
    hit = next(e for e in events if e.kind == "hit" and e.player == "X3Fire")
    assert hit.extra["subtype"] == "fire"
    assert hit.attacker == "FireplaceFireBarrel"
    assert hit.weapon == "FireplaceFireBarrel"


def test_adm_kill_ai_by_player():
    events, _ = _adm_events()
    kill = next(e for e in events if e.kind == "kill" and e.extra.get("victim_type") == "ai")
    assert kill.player is None
    assert kill.extra["victim_name"] == "Maria"
    assert kill.group == "37"
    assert kill.faction == "Raiders"
    assert kill.attacker == "Nomad"
    assert kill.weapon == "Mlock-91"
    assert kill.distance == 13.95


def test_adm_kill_player_by_mob():
    events, _ = _adm_events()
    kill = next(e for e in events if e.kind == "kill" and e.extra.get("victim_type") == "player")
    assert kill.player == "Gospod"
    assert kill.attacker == "ZmbF_VillagerOld_Red"
    assert kill.weapon is None


def test_adm_death_bleed_out_respawn():
    events, _ = _adm_events()
    death = next(e for e in events if e.kind == "death")
    assert death.player == "kuwalda"
    assert "Bleed sources: 0" in death.extra["stats"]

    bled = next(e for e in events if e.kind == "bleed_out")
    assert bled.player == "kuwalda"

    respawn = next(e for e in events if e.kind == "respawn")
    assert respawn.player == "RespawnGuy"


def test_adm_suicide_id_does_not_include_trailing_space():
    events, _ = _adm_events()
    suicide = next(e for e in events if e.kind == "suicide")
    assert suicide.player == "CMETAHA"
    assert suicide.bi_hash == "TpcpJ8IGVE+AGV5dZ1IXvyce76JmMIq4xHIhK+ucK+I="


def test_adm_ai_unconscious_state():
    events, _ = _adm_events()
    ai_state = next(e for e in events if e.kind == "unconscious")
    assert ai_state.extra["ai_name"] == "Eva"
    assert ai_state.extra["state"] == "unconscious"
    assert ai_state.group == "37"
    assert ai_state.faction == "Raiders"


def test_adm_chat():
    events, _ = _adm_events()
    chat = next(e for e in events if e.kind == "chat")
    assert chat.player == "Nomad"
    assert chat.text == "привет всем"
    assert chat.extra["channel"] == "Global"
    assert chat.ts.date() == date(2026, 11, 9)


def test_adm_midnight_rollover_advances_date_by_one_day():
    """Заголовок — 2026-11-09, строка после 00:00 должна получить 2026-11-10."""
    events, _ = _adm_events()
    disconnect = next(e for e in events if e.kind == "disconnect")
    assert disconnect.ts == datetime(2026, 11, 10, 0, 0, 5)
    assert disconnect.player == "Nomad"
    assert disconnect.steam64 == 76561199000000011
    assert disconnect.player_id == 76561199000000011
    # а не мусор позиции на дисконнекте (pos=<-3.4e38, ...>)
    assert disconnect.x is None
    assert disconnect.z is None


def test_adm_garbage_line_does_not_raise_and_counted_as_skipped():
    events, summary = _adm_events()
    assert len(events) == 14
    assert summary.parsed_lines == 14
    # "is connecting", [UpdateCheck] и мусорная строка без метки времени
    assert summary.skipped_lines == 3


# --------------------------------------------------------------------------
# 2. console.py
# --------------------------------------------------------------------------

def _console_events(base_date=date(2026, 11, 9)):
    summary = console.ParseSummary()
    events = list(
        console.parse_file(FIXTURES / "server_console.log", base_date=base_date, summary=summary)
    )
    return events, summary


def test_console_be_connect_event():
    events, _ = _console_events()
    be = next(e for e in events if e.kind == "be_connect")
    assert be.player == "Balabaster"
    assert be.extra["slot"] == 0
    assert be.ts == datetime(2026, 11, 9, 4, 3, 5)


def test_console_be_guid_identity():
    events, _ = _console_events()
    guid_event = next(e for e in events if e.kind == "identity" and "be_guid" in (e.extra or {}))
    assert guid_event.player == "Balabaster"
    assert guid_event.extra["be_guid"] == "0123456789abcdef0123456789abcdef"
    assert guid_event.extra["slot"] == 0


def test_console_steam_identity():
    events, _ = _console_events()
    steam_event = next(e for e in events if e.kind == "identity" and e.steam64 is not None)
    assert steam_event.player == "Balabaster"
    assert steam_event.steam64 == 76561199000000021
    assert steam_event.player_id == 76561199000000021


def test_console_default_base_date_is_today_without_base_date():
    events = list(console.parse_file(FIXTURES / "server_console.log"))
    be = next(e for e in events if e.kind == "be_connect")
    assert be.ts.date() == date.today()


def test_console_ip_and_rconpassword_never_leak_into_any_event_field():
    events, summary = _console_events()
    assert len(events) == 3
    secrets = ("10.0.0.5", "FAKE_SECRET_RCON_998877")
    for event in events:
        dump = " ".join(
            str(getattr(event, field_name))
            for field_name in ("text", "raw", "player", "kind", "extra")
        )
        for secret in secrets:
            assert secret not in dump, f"{secret!r} утекло в {event!r}"


def test_console_statemachine_fps_and_garbage_lines_are_skipped():
    events, summary = _console_events()
    assert summary.parsed_lines == 3
    # [StateMachine]..., FPS: 45.2, RConPassword=... (без времени), мусорная строка
    assert summary.skipped_lines == 4


# --------------------------------------------------------------------------
# 3. lbmaster.py
# --------------------------------------------------------------------------

def _lbmaster_events():
    summary = lbmaster.ParseSummary()
    events = list(
        lbmaster.parse_file(FIXTURES / "LBmaster_9_11_2026_04_00_05.log", summary=summary)
    )
    return events, summary


def test_lbmaster_date_taken_from_filename():
    events, _ = _lbmaster_events()
    join = next(e for e in events if e.kind == "join")
    assert join.ts == datetime(2026, 11, 9, 4, 0, 10)


def test_lbmaster_join_has_real_identity_name_and_steam64():
    events, _ = _lbmaster_events()
    join = next(e for e in events if e.kind == "join")
    assert join.player == "TerezVds"
    assert join.steam64 == 76561199000000031
    assert join.player_id == 76561199000000031
    assert join.extra["obfuscated_id"] == "123"


def test_lbmaster_leave_has_name_and_steam64():
    events, _ = _lbmaster_events()
    leave = next(e for e in events if e.kind == "leave")
    assert leave.player == "Joni"
    assert leave.steam64 == 76561199000000032


def test_lbmaster_admin_action_teleport():
    events, _ = _lbmaster_events()
    action = next(
        e for e in events if e.kind == "admin_action" and e.extra.get("subtype") == "teleport"
    )
    assert action.player == "Irina Drakylina"
    assert action.extra["target"] == "himself"
    assert action.extra["pos"] == "100.0, 200.0, 10.0"


def test_lbmaster_admin_action_spawn():
    events, _ = _lbmaster_events()
    action = next(
        e for e in events if e.kind == "admin_action" and e.extra.get("subtype") == "spawn"
    )
    assert action.player == "dimonbigboy"
    assert action.extra["item"] == "Lard"


def test_lbmaster_admin_action_delete():
    events, _ = _lbmaster_events()
    action = next(
        e for e in events if e.kind == "admin_action" and e.extra.get("subtype") == "delete"
    )
    assert action.player == "dimonbigboy"
    assert action.extra["item"] == "Lard"


def test_lbmaster_admin_action_esp():
    events, _ = _lbmaster_events()
    action = next(
        e for e in events if e.kind == "admin_action" and e.extra.get("subtype") == "esp"
    )
    assert action.extra["value"] is True


def test_lbmaster_admin_action_invisible():
    events, _ = _lbmaster_events()
    action = next(
        e for e in events if e.kind == "admin_action" and e.extra.get("subtype") == "invisible"
    )
    assert action.extra["value"] is True
    assert action.extra["target"] == "Irina Drakylina"


def test_lbmaster_admin_action_clear_inventory():
    events, _ = _lbmaster_events()
    action = next(
        e for e in events if e.kind == "admin_action" and e.extra.get("subtype") == "clear_inventory"
    )
    assert action.player == "dimonbigboy"
    assert action.extra["target"] == "Per_Duck"


def test_lbmaster_all_six_admin_subtypes_are_distinct():
    events, _ = _lbmaster_events()
    subtypes = {
        e.extra["subtype"] for e in events if e.kind == "admin_action"
    }
    assert subtypes == {"teleport", "spawn", "delete", "esp", "invisible", "clear_inventory"}


def test_lbmaster_chest_open():
    events, _ = _lbmaster_events()
    chest = next(e for e in events if e.kind == "chest_open")
    assert chest.player == "Per_Duck"
    assert chest.extra["pos"] == "120.0, 220.0, 12.0"


def test_lbmaster_garage_store_and_restore():
    events, _ = _lbmaster_events()
    store = next(e for e in events if e.kind == "garage_store")
    restore = next(e for e in events if e.kind == "garage_restore")
    assert store.steam64 == 76561199000000031
    assert store.extra["vehicle_class"] == "CrSk_UAZ_469"
    assert store.extra["vehicle_uid"] == "998877"
    assert restore.extra["vehicle_uid"] == "998899"


def test_lbmaster_watchlist_noise_and_garbage_line_are_skipped():
    events, summary = _lbmaster_events()
    assert len(events) == 11
    assert summary.parsed_lines == 11
    # "Received Watchlist/Banlist..." + мусорная строка без времени
    assert summary.skipped_lines == 2


# --------------------------------------------------------------------------
# 4. zf.py
# --------------------------------------------------------------------------

ZF_FIXTURE = FIXTURES / "zfbubusik_log-[09-11-2026] - 04;00;00.log"


def _zf_events():
    summary = zf.ParseSummary()
    events = list(zf.parse_file(ZF_FIXTURE, summary=summary))
    return events, summary


def test_zf_tagged_nickname_with_space_resolved_by_steam64_not_first_word():
    events, _ = _zf_events()
    moved = next(e for e in events if e.kind == "item_action" and e.extra["verb"] == "переместил")
    assert moved.player == "[NAPA] Shama"
    assert moved.steam64 == 76561199000000051


def test_zf_nested_container_path_parsed_as_chain_not_one_string():
    events, _ = _zf_events()
    moved = next(e for e in events if e.kind == "item_action" and e.extra["verb"] == "переместил")
    assert moved.extra["source_chain"] == ["Транспортное средство (Bastard_cj7_green)", "Одежда (Attack2Bag_Green)"]
    assert moved.extra["dest_chain"] == ["Персонаж", "Одежда (Attack2Bag_Green)"]
    assert isinstance(moved.extra["source_chain"], list)
    assert isinstance(moved.extra["dest_chain"], list)


def test_zf_item_action_pickup_from_ground():
    events, _ = _zf_events()
    picked = next(e for e in events if e.kind == "item_action" and e.extra["verb"] == "подобрал с земли")
    assert picked.player == "Balabaster"
    assert picked.steam64 == 76561199000000052
    assert picked.extra["item"] == "Rice"
    assert picked.extra["qty"] == 2
    assert picked.extra["item_id"] == "456"


def test_zf_item_action_pickup_from_distance():
    events, _ = _zf_events()
    picked = next(e for e in events if e.kind == "item_action" and e.extra["verb"] == "взял с расстояния")
    assert picked.extra["item"] == "Apple"
    assert picked.extra["distance"] == 3.0


def test_zf_vehicle_action_enter_and_exit():
    events, _ = _zf_events()
    enter = next(e for e in events if e.kind == "vehicle_action" and e.extra["verb"] == "сел в")
    exit_ = next(e for e in events if e.kind == "vehicle_action" and e.extra["verb"] == "вышел из")
    assert enter.extra["vehicle_class"] == "CrSk_UAZ_469"
    assert exit_.extra["vehicle_class"] == "CrSk_UAZ_469"
    assert enter.steam64 == 76561199000000052


def test_zf_action_engine_start():
    events, _ = _zf_events()
    action = next(e for e in events if e.kind == "action")
    assert action.extra["action"] == "Завести двигатель"


def test_zf_status_damage_from_infected():
    events, _ = _zf_events()
    status = next(
        e for e in events if e.kind == "status" and e.extra["verb"] == "получил урон от"
    )
    assert status.player == "Balabaster"
    assert status.extra["detail"] == "Зараженный"


def test_zf_unknown_subject_has_no_steam64():
    events, _ = _zf_events()
    killed = next(e for e in events if e.kind == "status" and e.extra["verb"] == "был убит")
    assert killed.player == "Неизвестно Неизвестно"
    assert killed.steam64 is None


def test_zf_roster_block_gives_one_event_per_player_with_position():
    events, _ = _zf_events()
    roster_events = [e for e in events if e.kind == "roster"]
    assert len(roster_events) == 2

    by_name = {e.player: e for e in roster_events}
    assert set(by_name) == {"Balabaster", "[NAPA] Shama"}

    balabaster = by_name["Balabaster"]
    assert balabaster.steam64 == 76561199000000052
    assert balabaster.x == 7609.4
    assert balabaster.z == 13458.6
    assert balabaster.extra["playtime"] == "0 д. 4 ч. 17 м."

    shama = by_name["[NAPA] Shama"]
    assert shama.steam64 == 76561199000000051

    # все события одного снимка — с одинаковым ts
    assert balabaster.ts == shama.ts == datetime(2026, 11, 9, 4, 7, 0)


def test_zf_container_move_marker_line():
    events, _ = _zf_events()
    move = next(e for e in events if e.kind == "container_move")
    assert move.player is None
    assert "перемещён между двумя контейнерами" in move.extra["notice"]
    assert "Attack2Bag_Green" in move.extra["details"]


def test_zf_vehicle_spawn_and_despawn():
    events, _ = _zf_events()
    spawn = next(e for e in events if e.kind == "vehicle_spawn")
    despawn = next(e for e in events if e.kind == "vehicle_despawn")
    assert spawn.extra["vehicle_class"] == "Bastard_cj7_green"
    assert spawn.extra["vehicle_id"] == "55"
    assert spawn.x == 8090.1
    assert spawn.z == 9330.9
    assert despawn.extra["vehicle_class"] == "Bastard_cj7_green"


def test_zf_colon_time_separator_also_parsed():
    """Строка 19 использует ':' вместо ';' как разделитель времени."""
    events, _ = _zf_events()
    cut = next(e for e in events if e.kind == "status" and e.extra["verb"] == "получил порез")
    assert cut.ts == datetime(2026, 11, 9, 4, 7, 40)


def test_zf_garbage_line_does_not_raise_and_counted_as_skipped():
    events, summary = _zf_events()
    assert len(events) == 18
    assert summary.parsed_lines == 18
    # заголовок "Список игроков:" + мусорная строка без метки времени
    assert summary.skipped_lines == 2


# --------------------------------------------------------------------------
# 5. carcodelock.py
# --------------------------------------------------------------------------

def _carcodelock_events():
    summary = carcodelock.ParseSummary()
    events = list(
        carcodelock.parse_file(FIXTURES / "CarCodeLock_2026-11-09_04-04.log", summary=summary)
    )
    return events, summary


def test_carcodelock_vehicle_open():
    events, _ = _carcodelock_events()
    opened = next(e for e in events if e.kind == "vehicle_open")
    assert opened.steam64 == 76561199000000041
    assert opened.player_id == 76561199000000041
    assert opened.extra["vehicle_class"] == "CrSk_UAZ_469"
    assert opened.x == 7609.4
    assert opened.z == 205.4
    assert opened.extra["pos"] == (7609.4, 13458.6, 205.4)
    assert opened.ts == datetime(2026, 11, 9, 4, 4, 20)


def test_carcodelock_vehicle_close():
    events, _ = _carcodelock_events()
    closed = next(e for e in events if e.kind == "vehicle_close")
    assert closed.steam64 == 76561199000000041
    assert closed.extra["vehicle_class"] == "CrSk_UAZ_469"
    assert closed.ts == datetime(2026, 11, 9, 4, 10, 0)


def test_carcodelock_garbage_line_does_not_raise_and_counted_as_skipped():
    events, summary = _carcodelock_events()
    assert len(events) == 2
    assert summary.parsed_lines == 2
    assert summary.skipped_lines == 1


# --------------------------------------------------------------------------
# Общее: ни один парсер не бросает исключение на мусорной строке
# --------------------------------------------------------------------------

def test_all_five_parsers_survive_garbage_lines_without_exception():
    adm_summary = adm_mod.ParseSummary()
    list(adm_mod.parse_file(FIXTURES / "sample.ADM", summary=adm_summary))

    console_summary = console.ParseSummary()
    list(console.parse_file(FIXTURES / "server_console.log", summary=console_summary))

    lbmaster_summary = lbmaster.ParseSummary()
    list(lbmaster.parse_file(FIXTURES / "LBmaster_9_11_2026_04_00_05.log", summary=lbmaster_summary))

    zf_summary = zf.ParseSummary()
    list(zf.parse_file(ZF_FIXTURE, summary=zf_summary))

    carcodelock_summary = carcodelock.ParseSummary()
    list(
        carcodelock.parse_file(
            FIXTURES / "CarCodeLock_2026-11-09_04-04.log", summary=carcodelock_summary
        )
    )

    for summary in (adm_summary, console_summary, lbmaster_summary, zf_summary, carcodelock_summary):
        assert summary.skipped_lines >= 1
