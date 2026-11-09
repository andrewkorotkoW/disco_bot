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
Player "X" (id=...)[HP: N] hit by Player "Y" (id=...) into LeftArm(18) for N damage ...   # PvP-урон на PVE-сервере!
Player "X" (id=...)[HP: N] hit by FallDamageHealth | FireplaceFireBarrel with FireDamage
Player "CMETAHA" (id=... ) committed suicide
Player "Gospod" (DEAD) (id=...) killed by ZmbF_VillagerOld_Red
Player "kuwalda" (DEAD) (id=...) died. Stats> Water: 3335 Energy: 4780 Bleed sources: 0
Player "kuwalda" (DEAD) (id=...) bled out
Player "X" (DEAD) (id=...) is choosing to respawn
AI "Maria" (DEAD) (group=37:"SZPlane_Raiders_1" faction="Raiders" pos=<...>) killed by Player "Nomad" (id=... group=26 faction="Zalupa" pos=<...>) with Mlock-91 from 13.95
AI "Eva" (group=37:"..." faction="Raiders" pos=<...>) is unconscious | regained consciousness | turned hostile to ...
[Chat - Global]("Nomad"(id=...)): текст сообщения
[UpdateCheck] All LBmaster mods are up to date !
```

Виды событий (kinds) для парсера: `connect`, `disconnect`, `hit` (с подтипами
infected/player/ai/fall/fire), `kill`, `death`, `suicide`, `bleed_out`, `unconscious`,
`respawn`, `chat`. В каждой строке с игроком: имя, id (BI-hash), group, faction, pos, HP.

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

Kinds: `admin_action` (teleport, spawn, delete, esp, invisible, …), `join`, `leave`,
`chest_open`. Нужен для тикетов «админ спавнил/телепортился», «кто открыл сейф».

## 4. `zfbubusik_log-[DD-MM-YYYY] - HH;MM;SS.log` — действия с предметами (мод ZF)

Строка: `[DD-MM-YYYY] - HH;MM;SS <Имя> <steam64> <глагол> <Предмет> (кол-во) (ID: …) …`.
Разделитель времени `;` (иногда `:`). Глаголы (по частоте): `переместил`, `подобрал с земли`,
`взял … с расстояния N м`, `положил`, `повесил на слот`, `снял со слота`, `бросил на землю`,
`сел в <машина> , x,y,z`, `вышел из`, `выполнил действие <…>` (напр. «Завести двигатель»),
`получил урон от`, `получил порез`, `заразился болезнью`, `выздоровел от болезни`,
`потерял сознание`, `был убит`. Субъект `Неизвестно Неизвестно` — ИИ/неигрок.

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
| «Пропал лут из базы/ящика» | ZF (переместил/подобрал/взял + `!!!` + Список игроков), LBmaster chest_open | предметы в радиусе от координат ± окно времени, ближайшие игроки по списку |
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
