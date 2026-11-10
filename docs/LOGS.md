# Логи сервера DayZ — форматы и что из них брать

Сервер: DayZ 1.30, PVE с ИИ-ботами (Expansion AI, фракции), хостинг CFTools Architect
(Windows, `...\Architect\Agent\deployments\0002\profiles\`). Рестарт каждые 4 часа
(00/04/08/12/16/20 по МСК) — каждая сессия даёт свой набор файлов. Время в логах
локальное (МСК, UTC+3), в заголовках script-лога указан и UTC.

Владелец выгружает три архива (образцы за 07–09.11.2026 лежат у владельца,
в git их НЕ класть — внутри IP игроков, BE GUID, пароль RCon):

| Архив | Содержимое | Нужно боту |
|---|---|---|
| `log_storage.rar` | папка `profiles` на каждую сессию: `<unix_ts>/*.ADM, *.RPT, server_console.log, LBmaster_*.log, error_*/info_*/script_*/crash_*.log, connection_log*, stats_log.txt` | **ADM, server_console, LBmaster**; crash/error — опционально |
| `_ZFServerLog.rar` | `zfbubusik_log-[DD-MM-YYYY] - HH;MM;SS.log` на сессию — мод-логгер действий с предметами (русский) | **да, главный источник по лутy** |
| `Logs.rar` | `CarCodeLock_YYYY-MM-DD_HH-MM.log` на сессию | **да** (транспорт) |

## 1. `DayZServer_x64_<date>.ADM` — админ-лог движка

Строка: `HH:MM:SS | <событие>`. Заголовок `AdminLog started on YYYY-MM-DD at HH:MM:SS`
даёт дату (в строках только время — дату брать из заголовка, учитывать переход через полночь).

```
Player "Kazah" (id=rsYnQ...H24=) is connecting
Player "Kazah" (id=rsYnQ...H24= pos=<4619.9, 8300.5, 323.5>) is connected
Player "Nomad" (id=7656119XXXXXXXXXX pos=<-3.4e38, ...>) has been disconnected      # на дисконнекте id = steam64, pos мусор
Player "Multik" (id=... group=22 faction="Zalupa" pos=<...>)[HP: 85.7] hit by Зараженный into Torso(1) for 6.175 damage (MeleeInfected)
Player "X" (id=...)[HP: N] hit by Player "Y" (id=... group=26 faction="Zalupa") into LeftArm(18) for N damage (ammo) with Mlock-91 from 13.95 meters    # PvP-урон на PVE-сервере; "meters" с trailing space — реальный формат!
Player "X" (id=...)[HP: N] hit by FallDamageHealth | FireplaceFireBarrel with FireDamage
Player "CMETAHA" (id=... ) committed suicide
Player "Gospod" (DEAD) (id=...) killed by ZmbF_VillagerOld_Red
Player "kuwalda" (DEAD) (id=...) died. Stats> Water: 3335 Energy: 4780 Bleed sources: 0
Player "kuwalda" (DEAD) (id=...) bled out
Player "X" (DEAD) (id=...) is choosing to respawn
AI "Maria" (DEAD) (group=37:"SZPlane_Raiders_1" faction="Raiders" pos=<...>) killed by Player "Nomad" (id=... group=26 faction="Zalupa" pos=<...>) with Mlock-91 from 13.95 meters
AI "Eva" (group=37:"..." faction="Raiders" pos=<...>) is unconscious | regained consciousness | turned hostile to ...
Player "Y" (id=... group=5 faction="..." pos=<...>) is unconscious | regained consciousness    # то же состояние бывает и у игрока, не только у AI
[Chat - Global]("Nomad"(id=...)): текст сообщения
[UpdateCheck] All LBmaster mods are up to date !
```

Виды событий (kinds) для парсера: `connect`, `disconnect`, `hit` (с подтипами
infected/player/ai/fall/fire), `kill`, `death`, `suicide`, `bleed_out`, `unconscious`,
`respawn`, `chat`. В каждой строке с игроком: имя, id (BI-hash), group, faction, pos, HP.

`hit`/`kill` несут `extra['attacker_kind']` (`player`/`ai`/`infected`/`fall`/`fire`/`env`) —
кто ударил/убил; для `hit` это дубль `extra['subtype']` (оставлен для старых тестов), для
`kill` поле новое. `unconscious` различает игрока и AI через `extra['subject_type']`
(`player`/`ai`) и несёт `extra['state']` (`unconscious`/`regained consciousness`) — у
`regained` в логе нет слова "is", в отличие от `is unconscious`.

## 2. `server_console.log` — консоль сервера

Строка: ` H:MM:SS <текст>` (без ведущего нуля). Полезное:

```
BattlEye Server: Player #0 Balabaster (IP:PORT) connected
BattlEye Server: Player #0 Balabaster - BE GUID: <hex32>
Player "Balabaster" (steamID=7656119XXXXXXXXXX pos=<...>) is connected
[StateMachine]: Player espanol (dpnid N uid rql2k...) ...
```

Это **единственное место, где имя, steam64 и BI-hash (uid) связаны в момент коннекта** —
основа для резолвера идентичностей. Остальное (FPS, [CE] спавн лута, BattlEye конфиг) — шум.
В логе есть `RConPassword` открытым текстом — файл не выкладывать и не индексировать строку.

## 3. `LBmaster_D_M_YYYY_HH_MM_SS.log` — LBmaster (AdminTools, Virtual Garage, Loot Chests)

Строка: `HH:MM:SS [LEVEL] [Module] текст`.

```
[ADMIN] [Core] Player Joined with Obfuscated ID: 123 Real Identity: TerezVds (76561198...)
[ADMIN] [Core] Player Joni (76561198...) left the server
[ADMIN] [AdminTools] Admin Irina Drakylina (N) Teleported himself to <x, y, z>
[ADMIN] [AdminTools] Admin dimonbigboy (N) spawned item Lard at <...> | deleted item ... | toggled ESP: true | set Modifier INVISIBLE to true for ...
[INFO ] [LootChests] Player Per_Duck (N) opened chest at ...
[INFO ] [AdminTools] Received Watchlist/Banlist from webserver   # шум, каждую минуту
```

Virtual Garage/Loot Chests — реальные форматы (выяснены по живым логам владельца, а не
придуманы заранее):
```
[ADMIN] [VirtualGarage] Vehicle Stored: Bastard_cj7_green by dimonbigboy (76561198...) at <8090.1, 458.9, 9330.9> TraderID: 0 Had Insurance: false Insurance Value: 0
[ADMIN] [VirtualGarage] Vehicle Restored: Bastard_cj7_green by dimonbigboy (76561198...) at <8090.1, 458.9, 9330.9> TraderID: 0 VehicleUID: 2004113087 Infinite ? false. Vehicle was in Garage for 2 h 30 min Insurance: false Value: 0
[ADMIN] [VirtualGarage] New Public Garage was placed by Irina Drakylina (76561198...) at <x, y, z> GarageID: 17
[ADMIN] [VirtualGarage] Admin Trader was deleted by Irina Drakylina (76561198...) at <x, y, z> UID: 9
[INFO ] [LootChests] Player Per_Duck (76561198...) opened chest at <x, y, z> with ExpansionCodeLockKit Successrate: 0.42 Rand: 0.31
[ADMIN] [LootChests] Player Per_Duck (76561198...) successfully opened chest ExpansionSafeBig for Safe big at <x, y, z>
```
`Vehicle Stored` **не содержит `VehicleUID`** — оно есть только у `Restored`; это
свойство реального лога, а не пропуск в парсере (`garage_store` без ID машины —
`upsert_vehicle` на нём не пишет строку в таблицу `vehicles`). Слово перед
`Stored`/`Restored` не всегда "Vehicle" (бывает и "Bike") — регекс не привязан к нему.

Kinds: `join`, `leave`, `admin_action` (подтипы `teleport`/`spawn`/`delete`/`esp`/
`invisible`/`clear_inventory` в `extra['subtype']`), `chest_open` (подтипы
`attempt`/`success` в `extra['subtype']`; у `attempt` — `pos`/`tool`/`successrate`/`rand`,
у `success` — `pos`/`chest_name`/`chest_config`), `garage_store`/`garage_restore`
(`vehicle_class`, `trader_id`, `pos`; у `garage_restore` ещё `vehicle_uid`,
`in_garage_for`, `infinite`, `insurance`, `value`), `garage_placed`/`garage_deleted`
(`pos`, `garage_id`/`uid`). Нужен для тикетов «админ спавнил/телепортился», «кто открыл
сейф», «кто ставил/убирал машину в гараж».

## 4. `zfbubusik_log-[DD-MM-YYYY] - HH;MM;SS.log` — действия с предметами (мод ZF)

Строка: `[DD-MM-YYYY] - HH;MM;SS <Имя> <steam64> <глагол> <Предмет> (кол-во) (ID: …) …`.
Разделитель времени `;` (иногда `:`). Глаголы (по частоте): `переместил`, `подобрал с земли`,
`взял … с расстояния N м`, `положил`, `повесил на слот`, `снял со слота`, `бросил на землю`,
`сел в <машина> , x,y,z`, `вышел из`, `выполнил действие <…>` (напр. «Завести двигатель»),
`получил урон от`, `получил порез`, `заразился болезнью`, `выздоровел от болезни`,
`потерял сознание`, `был убит`. Субъект `Неизвестно Неизвестно` — ИИ/неигрок.

`item_action`/`container_move` разбирают хвост строки на цепочки контейнеров
(`src_chain`/`dst_chain`, путь вида `Персонаж (X) -> Одежда (Y)`, звенья разделены
` -> `) и берут из них `src_root_class`/`dst_root_class` — класс **корня** цепочки
(машина/игрок/мебель), а не любое звено пути; именно по корню, а не по подстроке во всей
цепочке, отличается «предмет из машины» от «предмет из сумки в машине». Готовый
читаемый `text`/`raw` события — исходная строка целиком, с глаголом (переместил/
подобрал/взял/положил/повесил/снял/бросил) и названием предмета, его не нужно
пересобирать из `extra` для показа в разборе тикета.

Машины дублируют появление/исчезновение в мире (не только посадку/высадку):
```
Транспортное средство Bastard_cj7_green (ID: 55) инициализировано по координатам 8090.1,458.9,9330.9
Транспортное средство Bastard_cj7_green (ID: 55) исчезло из игрового мира по координатам 8090.1,458.9,9330.9
```
Kinds `vehicle_spawn`/`vehicle_despawn`, `extra['vehicle_class']`/`['vehicle_id']`/
`['pos']`. "исчезло" на координатах гаража = машину убрали в Virtual Garage (даже без
LBmaster-лога), "инициализировано" в начале сессии = машина просто стояла в мире на
рестарте (см. «Уроки разбора...» ниже).

`death` (`был убит...`) разбирает хвост, когда формат строки это позволяет:
`extra['killer_kind']` (`player`/`bot`/`env`), `killer_name`, `killer_steam64` (только
для `player`), `weapon`, `cause` (сырой текст «Причина смерти»), `distance`, `pos`
(позиция жертвы). Пример:
```
[09-11-2026] - 16;28;00 Paul Piski 76561198000000099 был убит. Игрок: Nomad (76561198000000001) с Mlock-91, Расстояние: 13.95 м., Причина смерти: Соскучился по пляжу, Позиция жертвы: 100.0,200.0,300.0
```
Убийцу-бота отличает `Бот EAi: <имя> с <оружие>` (`killer_kind=bot`, без steam64).
«Причина смерти» здесь — не название оружия: `Соскучился по пляжу` и похожие фразы —
ярлык обычной смерти/кровопотери без убийцы (суицид либо истёк кровью), см. «Боты...»
ниже.

Служебные блоки:
```
[..] - HH;MM;SS - Список игроков:
Игрок: Balabaster (ID: 7656119XXXXXXXXXX), Местоположение: <7609.4, 205.4, 13458.6> - , Время в игре: 0 д. 4 ч. 17 м.
...
[..] - HH;MM;SS !!!предмет был перемещён между двумя контейнерами посмотри по координатам списка игроков, кто был ближе к событию!!! <детали>
```
Список игроков с позициями пишется периодически — по нему и по `!!!` строкам решаются
тикеты «пропал лут из базы/ящика»: кто был ближе всех к координатам в это время.
Имя может содержать тег и пробел (`[NAPA] Shama`) — парсить по steam64 (17 цифр), а не по
первому слову. Порядок координат здесь `x, y(высота), z` — не как в ADM (`x, z, y`)! Сверять.

## 5. `CarCodeLock_YYYY-MM-DD_HH-MM.log`

```
[2026-11-08 04:04:20] [INFO] [CarCodeLock] Player: '7656119XXXXXXXXXX', open transport: 'CrSk_UAZ_469', on position: 'x y z'
[...] close transport: ...
```
Kinds: `vehicle_open`, `vehicle_close`. Вместе с ZF `сел в/вышел из` — тикеты про угон.

## 6. Остальное

- `*.RPT` (до 9 МБ/сессию) — движок, предупреждения конфигов; для тикетов бесполезен.
- `error_*.log` — ошибки Expansion Market config; `crash_*.log` — «Virtual Machine Exception»
  скриптов; `info_*/script_*` — старт модов. Полезны только для тикетов «сервер упал/лагает».
- `connection_log*`, `stats_log.txt`, `system*.txt`, `appinfo_log.txt`, `configstore_log.txt` — Steam-шум.

## Соглашение о времени

Все парсеры (`app/logs/*.py`) кладут в `LogEvent.ts` **naive** `datetime` в местном
времени сервера (МСК), без `tzinfo` — ровно то, что написано в строке лога. Индекс
(`SqliteLogSource`) хранит и сравнивает `ts` как есть, без привязки к UTC.

`app/logs/facts.py` (`resolve_time_window`/`_parse_time_hint`, используются из
`app/bot/handlers.py` для вычисления окна `since`/`until` вокруг времени инцидента)
следуют тому же соглашению и тоже работают с naive local time. Раньше
`resolve_time_window` возвращал aware `datetime` с `tzinfo=timezone.utc`; при сравнении
с naive `ts` в индексе (ISO-строка с `+00:00` против строки без пояса) получался сдвиг —
это исправлено: теперь всё время в модуле naive. При сборке собственных окон/фильтров по
времени нужно строить `datetime(...)` без `tzinfo`, а не `datetime.now(timezone.utc)`.

Переменная `SERVER_TZ` (по умолчанию `Europe/Moscow`, читается в `app/logs/facts.py`)
называет, какой часовой пояс имеется в виду под «местным временем» — чисто для справки
в коде; сама конвертация времени по ней не делается (конвертации нет вовсе — везде один
и тот же пояс). В `.env.example` переменная не заведена.

## Идентичности игрока

| Источник | Идентификатор |
|---|---|
| ADM connect/hit/kill/chat | BI-hash (`rsYnQgp3…H24=`, base64 44 символа) |
| ADM disconnect | steam64 |
| server_console | имя + steam64 + BI uid + BE GUID + IP |
| ZF, CarCodeLock | steam64 |
| LBmaster | «Obfuscated ID» + `Real Identity: имя (steam64)` |

Нужна таблица `identities(name, steam64, bi_hash, be_guid, first_seen, last_seen)`, которая
наполняется с коннектов в server_console и ADM; поиск по тикету — по любому из трёх ключей
(имена меняются и дублируются, steam64 — главный ключ).

## Что бот должен уметь доставать по тикету

| Тип тикета | Источники | Запрос |
|---|---|---|
| «Пропал лут из базы/ящика» | ZF (переместил/подобрал/взял + `!!!` + Список игроков), LBmaster chest_open; `container_access(класс)` в индексе (`app/logs/sqlite_source.py`) — кто брал/клал по корню цепочки контейнеров | предметы в радиусе от координат ± окно времени, ближайшие игроки по списку; CLI `python -m app.logs container <класс>` для ручной проверки |
| «Меня убил игрок на PVE» | ADM `hit by Player`, `killed by Player` | по жертве/атакующему ± 10 мин, дистанция, оружие |
| «Угнали машину» | CarCodeLock, ZF `сел в/вышел из`, ZF `Завести двигатель` | по типу машины/координатам ± окно |
| «Админ абьюзит» | LBmaster admin_action | по админу ± окно |
| «Умер непонятно от чего / потерял шмот» | ADM death/bleed_out/suicide/hit, ZF `получил урон` | по игроку ± 15 мин до смерти |
| «Вылетел/сервер упал» | crash_*, server_console FPS, ADM disconnect | по времени |

## Уроки разбора реального кейса (09–10.11.2026, «лут из CJ7»)

- **ID предметов не переживают Virtual Garage.** После `Vehicle Restored` все предметы в машине
  получают новые `(ID: …)`; VehicleUID у машины тоже другой. Сопоставлять содержимое до/после
  гаража можно только по классу+количеству и времени, не по ID. Вложенные предметы (сумка в машине,
  предмет в слоте сумки) после восстановления на месте — гараж их сохраняет.
- **Вложенные контейнеры в ZF**: `Транспортное средство (Bastard_cj7_green) -> Одежда (Attack2Bag_Green)` —
  путь контейнера, парсить как цепочку через ` -> `, источник и приёмник разделены ` в `.
- **Один и тот же предмет (Attack2Bag) бывает на игроке и в машине** — `Персонаж -> Одежда (Attack2Bag_Green)`
  vs `Транспортное средство -> Одежда (Attack2Bag_Green)`; различать по корню пути.
- **Проверка «кто был рядом, пока владелец офлайн»**: окна офлайна из ADM (disconnected→connected),
  позиция машины из последнего CarCodeLock `close transport`, затем Список игроков ZF в радиусе N м.
  Для 08.11 15:41 так нашёлся админ Irina Drakylina в 3 м от машины (телепорт + INVISIBLE по LBmaster,
  82 секунды, без действий с предметами) — админский обход баз, не кража.
- **Сессия 09.11 20:00 оборвалась в 20:47–20:48** (массовый `left the server` за минуту, следующая
  сессия с 20:50) — это краш, не плановый рестарт; для последней сессии в выгрузке нет ADM и
  LBmaster (сервер ещё работал), есть только ZF и CarCodeLock.
- `исчезло из игрового мира` для машины на координатах гаража (8090.1, 458.9, 9330.9) = убрана в гараж
  (даже без LBmaster-лога), `инициализировано` в начале сессии = машина стояла в мире на рестарте.

## Уроки разбора «лут пропадает из мебели rag_baseitems» (10.11.2026)

- Взаимодействие с мебелью ZF пишет как `взял/положил/переместил … Контейнер (rag_baseitems_X)`;
  строк `повесил на слот`/`снял со слота` с контейнером-мебелью нет вообще (только с Персонаж).
  `rag_baseitems_trashcan_static` — мусорка на хабе (уничтожение), в статистику «пропаж» не брать.
- **ID предметов регенерируются не только в гараже**: содержимое BRDK_PersonalChest_* после рестарта
  выходит с новыми ID в 100 % случаев (864 новых / 147 известных) — это мод-хранилище. Вывод
  «предмет не виден после рестарта → пропал» по ID делать нельзя; нужна проверка класс+количество+
  место, либо контрольный эксперимент (повесить дешёвый предмет, дождаться рестарта).
- Метод «положено до рестарта → снова видно после» по ID годится только для ванильных контейнеров
  (SeaChest: 55 известных / 12 новых на выходе) и kr_furn (BarrelShelf 44 % виден снова).
- RPT `ENTITY: Load entity type 'X'` на рестарте — НЕ инвентарь сохранённых объектов (kr_furn там
  нет, хотя они персистентны); по нему нельзя судить, сохранилась ли мебель.
- Жалоба «стволы пропали» (Paul Piski 08.11 16:37) при разборе оказалась смертью у ангара СЗ
  от ботов — оружие с трупа исчезло за 30 минут без единой записи «подобрал» у игроков.
  Отдельный паттерн тикета: «умер у ботов — лут с трупа пропал» (боты/деспаун трупа), не кража.

## Боты (Expansion eAI / AI Missions) и лут игроков (10.11.2026)

- **Боты лутают.** Строки `!!!предмет был перемещён между двумя контейнерами…` с субъектом
  `Персонаж (eAI_…)` — это действия ботов: за 2 дня 3022 строки, из них 675 `подобрал с земли`
  (оружие вплоть до AKM/AK74/SVD/Mosin, магазины, патроны, одежда — боты переодеваются, медикаменты).
  «с расстояния 11151 м» в этих строках — артефакт, расстояние не учитывать; координаты в конце — позиция бота.
- **Подбор ботом даёт предмету новый ID** (4EMODAH бросил Saiga #-731924401 в 18:23:34, бот поднял
  Saiga #1225337251 в 18:23 на тех же координатах). Поэтому «ствол по ID больше не виден» ≠ «пропал»;
  искать подбор ботом надо по классу + координаты трупа ± 10 м + время ± 10 мин.
- Из 151 смертей игроков за 2 дня 17 — от ботов; в 9 случаях боты подобрали вещи у трупа в течение
  10 минут (включая Saiga, FNX45, AK74 ×3, FAL, R12). Бот с трофеем потом деспаунится
  («No players in spawn radius, despawning EAI NPC» в script-логе AI Missions) — лут исчезает насовсем.
- Бывает и обратное: оружие с трупа исчезает без единой записи о подборе (Paul Piski, 08.11 16:28,
  AKM+SV98 у ангара СЗ, владелец вернулся через 29 мин) — причина в логах не видна (чистка/лайфтайм).
- `Причина смерти: Соскучился по пляжу` в ZF — это обычная смерть/кровотечение без убийцы (суицид
  или истёк кровью), не название оружия. Причина с ботом: `Бот EAi: eAI_SurvivorM_X с <оружие>`.
- Субъекты `Персонаж (SurvivorM_Guo)` (без eAI_) в `!!!`-строках, складывающие вещи в машину-награду —
  NPC мода AI Missions, не игроки.
