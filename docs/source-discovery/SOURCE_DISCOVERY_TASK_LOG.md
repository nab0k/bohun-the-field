# BOHUN Defence — Source Discovery Task Log

> **Scope:** поиск, проверка и каталогизация публичных источников информации для глобальной оборонной и dual-use разведки BOHUN Defence. **Не** общий журнал разработки сайта.
>
> **Создано:** 2026-10-24. **Статус:** первоначальный реестр для сверки, не аудит текущего состояния. **Ветка:** `docs/source-discovery-task-log-20261024`. Основная ветка и production не менялись.
>
> **Основание:** обсуждение Source Discovery в чате; исторический снимок `BOHUN-local/todo/TODO_INDEX.md` от 2026-10-17 (не текущая проверка), ранее упомянутый `SOURCE_HARVEST_V1`. Все количественные показатели требуют сверки с актуальными данными.

## Цель и границы

Сформировать проверенный, классифицированный реестр источников по странам, типам и тематике: профильные СМИ, закупки/тендеры, гранты, инвестиции, компании и стартапы, выставки, регуляторика, think tanks и публичные профессиональные сообщества.

**Не путать этапы:** обнаружение → проверка → реестр → подключение/сбор → ИИ-обработка → аналитика → редактура → публикация. Этот журнал ведёт первые три этапа и фиксирует зависимости от четвёртого.

## Статусы

- 🟡 IN_PROGRESS — есть работа, завершение не подтверждено.
- 🔎 VERIFY — требуется сверка с актуальным источником истины.
- ⏸️ PAUSED — отложено, не блокирует текущую цель.
- ⛔ BLOCKED — есть подтверждённая блокировка.
- ✅ DONE — выполнено и подтверждено ссылкой/проверкой.
- ⬜ TODO — не начато.

## Реестр задач

| ID | Задача | Статус | Подтверждение / пробел | Следующее действие |
|---|---|---|---|---|
| SD-001 | Инвентаризация текущих источников и подписок | 🟡 IN_PROGRESS | Notion BOHUN Sources: 166 документов, 151 уникальный URL (проверено 2026-10-24); Inoreader и feed registry не проверены | Сверить подписки Inoreader, GitHub и каталог фидов; не считать evidence docs RSS |
| SD-002 | Матрица покрытия по странам, темам и типам источников | 🟡 IN_PROGRESS | Первичная карта тематических пробелов; полнота по странам не измерена | После live readback собрать source-level матрицу |
| SD-003 | Поиск недостающих источников | 🟡 IN_PROGRESS | Историческая работа Source Discovery; нет подтверждённого текущего перечня | Продолжить поиск по пробелам SD-002 |
| SD-004 | Валидация RSS/API/страниц и качества данных | 🟡 IN_PROGRESS | Исторические проверки, нужна повторная выборочная валидация | Зафиксировать критерии и протокол проверок |
| SD-005 | Единый реестр источников и дедупликация | 🔎 VERIFY | Исторические данные о реестре, актуальная структура не сверена | Найти источник истины и определить недостающие поля |
| SD-006 | Подключение проверенных источников к постоянному сбору | ⏸️ PAUSED | Зависит от SD-003–005; относится к Source Collection | Вернуться после готовности приоритетного каталога |
| SD-007 | Inoreader OAuth Token Manager | ⏸️ PAUSED | Техническая подзадача SD-006; изолированный прототип в GitHub | Не развивать до решения о возобновлении SD-006 |

## Журнал событий и решений

| Дата | Задача | Событие | Проверка / статус |
|---|---|---|---|
| 2026-10-17 | SD-001, SD-004 | Исторический снимок TODO упоминает `SOURCE-HARVEST-V1` и ограничения прежних проверок | Снимок, не актуальная верификация |
| 2026-10-23 | SD-007 | Прототип OAuth Token Manager в отдельной GitHub-ветке | Код создан; результаты локального запуска тестов в этом журнале не подтверждены |
| 2026-10-24 | SD-001–007 | Решено вернуть приоритет Source Discovery; OAuth v2 приостановлен | Решение из беседы |
| 2026-10-24 | SD-001–007 | Создан специализированный журнал задач Source Discovery | Этот файл, изолированная ветка |

## Частичная инвентаризация 2026-10-24

Создана [таблица Notion SD-001](https://app.notion.com/p/28b981422d8b47fb80b4c98e582228ee) в разделе Sources Intelligence. Из существующей [BOHUN | Sources](https://app.notion.com/p/72caef14115d459bb8e0d0f7eafd0a33) прочитана схема и выполнены read-only агрегаты: **166 записей, 151 уникальный Source URL, 0 пустых URL**. Разбивка: COMPANY 101, OTHER 27, OFFICIAL_REGISTER 19, OFFICIAL_PROGRAMME 5, COUNTERPARTY 5, TRADE_PRESS 4, FINANCIAL_REPORT 3, PATENT_REGISTER 1, без типа 1. Сумма 166. Эти записи являются документами-доказательствами и **не означают 166 источников ленты**. Наличие URL не подтверждает доступность RSS или актуальность документа.

Созданы 12 строк инвентаризации Notion: 10 агрегатов и 2 непроверенных направления (Inoreader subscriptions и покрытие стран). **SD-001 остаётся открытой** до проверки реального каталога feed subscriptions, дедупликации между системами и оценки географического/тематического покрытия. Изменений в исходной базе BOHUN | Sources не было.

| 2026-10-24 | SD-001 | Read-only подсчёт 166 документов/151 URL, создана таблица Notion из 12 строк | Частичный результат; Inoreader не проверен |

## SD-001 / SD-002: сопоставление источников, 2026-10-24

**Подтверждено read-only запросом к Supabase:** 5 372 записи intelligence.items содержат raw_data.origin_title, 48 уникальных названий publishers, 58 уникальных origin_stream_id; newest published_at 2026-10-24 11:00:22 UTC. Это историческая доставка контента из потоков, **не live-список текущих подписок**. Среди реально представленных издателей: Ukrinform (1015), Defense News (401), Tech.eu (287), UK MOD GOV.UK activity (285), Defence Industry Europe (261), Targi Kielce (254), Sifted (209), DASA GOV.UK (200), UK Defence Journal (198), Militär Aktuell (177), govinfo (172), Defence24 (157), Agoria (136), Breaking Defense (113), Naval News (62), NATO Innovation Fund (5), TEKEVER (3). Данные показывают наличие материалов, но не проверяют нынешнюю доступность каждого feed.

**Кандидаты:** [Top 100 Inoreader](https://app.notion.com/p/3f4c42d0b11b8102a8cfdb15b78ed3dd) = 100 curated candidates, не подтверждённые подписки. [Intelligence Network](https://app.notion.com/p/3f6c42d0b11b80e4a06edf6483ae6954) фиксирует 65 EDA/ENDR directory entries по 28 кодам стран (38 ассоциаций, 10 мероприятий, 17 кластеров), не 65 feed URL. Историческое сообщение о 67 подписках не подтверждено текущим API readback. **Точный список "найдены, но не подключены" установить без live Inoreader subscriptions export нельзя.** Отсутствие в historical items не доказывает отсутствие подписки.

**Пробелы (статус: недоказанное/неполное покрытие, не "0 источников"):** национальные закупки и контракты за пределами TED; гранты и EU Funding API; отдельные event calendars; научные публикации и R&D; инвестиции и раунды; регуляторика/санкции/экспортные правила; think tanks и публичные профессиональные форумы. По исторической архитектуре TED collector реализован, Grants.gov collector реализован, но требует проверки; Events/Research/Investment и EU Funding API collectors не реализованы на дату записи. Текущее состояние кода/запусков ещё требует отдельного аудита. News и corporate пресс-релизы представлены, но полнота по странам не измерена.

**Notion:** [SD-001 inventory](https://app.notion.com/p/28b981422d8b47fb80b4c98e582228ee) дополнена 7 строками о historical ingestion, кандидатах и пробелах. Следующее действие: read-only live Inoreader subscriptions export, нормализация canonical feed URL, join с Top100/65 leads, проверка по категориям и странам; не изменять подписки.

| 2026-10-24 | SD-001, SD-002 | Supabase read-only: 5372 items / 48 publishers / 58 streams; Notion Top100 и 65 leads; категории пробелов | Частично проверено; live Inoreader readback отсутствует |

## Правило исполнения задач — принято 2026-10-24

**FOCUS-001: Одна активная задача до проверяемого завершения.**

1. В каждый момент есть одна **ACTIVE** задача с ID, целью, критериями готовности и следующим конкретным действием. Не переключаться на другую, если это не требуется для решения ACTIVE.
2. Любую обнаруженную побочную задачу немедленно заносить в **Backlog / Parking Lot** с ID, датой, причиной, приоритетом, зависимостями и точкой возврата. Не начинать её без завершения ACTIVE либо явно согласованного изменения приоритета.
3. Все действия по ACTIVE фиксировать в этом журнале: что проверялось, что сделано, доказательство (URL/запрос/артефакт), результат, ограничения, ошибки, решения, оставшиеся пункты и следующий шаг. Не скрывать незавершённые проверки и не выдавать агрегаты за построчную инвентаризацию.
4. Статус **DONE** только при выполнении заранее зафиксированных критериев готовности и проверяемом readback. При блокировке записать причину, попытки, варианты и запросить решение, не уходить молча в другую работу.
5. Перед сменой задачи написать **handoff/checkpoint**: итог, критерии готовности и подтверждения, остатки, ссылки на Notion/GitHub, следующая ACTIVE. Возвращаться к побочным задачам только после этого.
6. Все решения пользователя, включая изменение критериев качества источников, и каждое изменение статуса записывать с датой. Журнал является источником истины о ходе Source Discovery; Notion содержит построчные данные.

**ACTIVE: SD-001 — полная построчная инвентаризация исходных списков источников из Notion и GitHub.** Критерии готовности: (а) извлечены все доступные записи из выявленных списков; (б) нормализованы названия/URL и выявлены дубли; (в) каждая уникальная запись есть в Notion с происхождением, категорией, страной, аналитической ценностью, типом доступа к полному тексту и статусом подключения (unknown, если не подтверждено); (г) числа сверены с реестрами и прочитан обратно результат; (д) журнал содержит доказательства и нерешённые ограничения. **SD-002 не начинать до закрытия SD-001.**

**Решение пользователя 2026-10-24:** Ukrinform — низкоприоритетный общий поток военных новостей, не ведущий источник BOHUN. Приоритет по ценности defence/dual-use сигналов, а не количеству статей. Notion и существующий GitHub registry проверять прежде Inoreader API. Неполный RSS-текст не считать достаточным первоисточником для аналитических выводов.

### Backlog / Parking Lot

| ID | Дата | Побочная задача | Причина / зависимость | Когда вернуться | Статус |
|---|---|---|---|---|---|
| SD-002 | 2026-10-24 | Матрица пробелов по странам/категориям | Нужен проверенный реестр SD-001 | После DONE SD-001 | PARKED |
| SD-006 | 2026-10-24 | Проверка Inoreader live subscriptions и полноты текста | API не требуется для извлечения исходных списков; может понадобиться для подтверждения подключения | После построчной инвентаризации или если без API нельзя закрыть конкретный критерий SD-001 | PARKED |
| SD-007 | 2026-10-24 | OAuth Token Manager | Не помогает сейчас найти и классифицировать источники | После решения по SD-006 | PARKED |

## SD-001 checkpoint — 2026-10-24, Top 100 migrated

**Выполнено:** из [Notion Top 100](https://app.notion.com/p/3f4c42d0b11b8102a8cfdb15b78ed3dd) извлечены 100 построчных кандидатов с URL, названием, географией и разделом исходного списка. Создан отдельный [BOHUN | Source Registry — SD-001](https://app.notion.com/p/ce41b2f029ef4794853f1448ca33112b), чтобы не смешивать источники с агрегатами [Inventory](https://app.notion.com/p/28b981422d8b47fb80b4c98e582228ee). Все 100 записей созданы, SQL readback подтвердил **100 rows / 100 distinct Website / 100 distinct Name**. Нормализация по hostname выявила **97 distinct domains** (повторяются defence-industry-space.ec.europa.eu, gov.uk, kielinstitut.de); это совпадения доменов, **не доказанные дубли**.

**Ограничения:** Connection=Unknown, Content access=Unverified, Value=Unrated; не выполнялась верификация RSS/API, полного текста или live-подписки. Категории пока предварительно перенесены из разделов исходного документа (в смешанном разделе Investments/events требуют уточнения). Приоритет Ukrinform как общего потока новостей снижен концептуально, не как изменение строки Top100 (его там нет).

**Осталось до DONE SD-001:** извлечь GitHub registry v1 (исторически 109 = 16 accepted + 93 candidates), остальные источники из Intelligence Network (EDA/ENDR 65 записей, проверить пригодность как feeds), сопоставить с Top100 по canonical URL, отделить entities от источников, заполнить построчный статус и качество с явными unknown, сверить итоги. Inoreader API не вызывать без необходимости.

| 2026-10-24 | SD-001 | 100 источников из Top100 записаны в отдельную построчную Notion DB; readback 100/100, 97 доменов | Частично DONE (пакет), SD-001 ACTIVE |

## SD-001 checkpoint — 2026-10-24, GitHub web-monitor inventory

Из GitHub **main** прочитан реальный конфигурационный файл [collectors/web_monitor/sources.json](https://github.com/nab0k/bohun-the-field/blob/main/collectors/web_monitor/sources.json): **14 source configurations / 13 distinct configured URLs**, все status=candidate_unverified, enabled=true. Сопоставление с 100 записями Notion Top100 по точному case-insensitive имени: **3 совпадения** (Defense Innovation Unit, Polish Ministry of National Defence, European Defence Agency), **11 новых имён**. Эти 11 добавлены в [построчный Notion registry](https://app.notion.com/p/ce41b2f029ef4794853f1448ca33112b) с происхождением GitHub и статусом Unverified. SQL readback: **111 rows / 108 distinct Website**. Повторы URL не обязательно дубли источников (например Bundeswehr и BAAINBw на одном URL).

**Найдено в историческом [Autonomous Intelligence Pipeline checkpoint](https://app.notion.com/p/3f4c42d0b11b81cd8f8bca6ea9884d20):** registry v1 109 = 16 accepted + 93 candidates и 40 Inoreader subscriptions по состоянию 2026-10-09. Это **исторические цифры, не live состояние**. Сам файл registry v1 в GitHub ещё не локализован. GitHub code search не дал результатов для source_registry/sources.json; не следует считать его отсутствующим. Следующий шаг SD-001: найти точный путь registry v1, проверить 109 строк и провести дедупликацию, затем EDA/ENDR.

**Согласованный порядок:** сначала завершить инвентаризацию исходных списков Notion/GitHub, **затем проверить фактическое состояние Inoreader** (read-only API subscriptions), так как пользователь прямо запросил это после текущего шага. Проверку Inoreader занести в SD-001 как необходимую верификацию статуса подключения; OAuth repair остаётся parked, если не понадобится.

| 2026-10-24 | SD-001 | GitHub web-monitor: 14 configs, 3 overlap, 11 new; Notion readback 111 rows/108 URLs; исторический registry 109 не найден | SD-001 ACTIVE, partial checkpoint |

## SD-001 checkpoint — 2026-10-24, registry search / EDA evidence / Inoreader access

**Новые проверенные сведения:** GitHub main содержит [docs/sources/country-source-leads-batch01-20261010.csv](https://github.com/nab0k/bohun-the-field/blob/main/docs/sources/country-source-leads-batch01-20261010.csv): 37 строк кандидатов (Austria и Belgium), не подтверждённых RSS/API. [Intelligence Network](https://app.notion.com/p/3f6c42d0b11b80e4a06edf6483ae6954) документирует EDA/ENDR 65 кандидатов (38 ассоциаций, 10 выставок, 17 кластеров), из них исторически только 5 сайтов проверены, 0 live RSS/API. В том же документе упомянуты старые локальные экспорты `bohun_registry_v2/source_registry_v2.csv` (100) и `inoreader-source-report(1).zip` (42 subscriptions в старом dry-run), **но они не доказаны присутствующими в GitHub или доступными в этой сессии**. Попытки fetch GitHub `bohun_registry_v2/source_registry_v2.csv` и `docs/sources/source_registry_v1.csv` вернули 404. Historical registry 109 из checkpoint пока не локализован. Нельзя объявлять его сверенным.

**Inoreader:** поиск доступных подключаемых приложений по Inoreader дал пустой результат; отдельного Inoreader-коннектора в текущих инструментах нет. Исторические цифры 40/42/67 нельзя считать live. Для read-only сверки понадобится актуальный экспорт OPML/список подписок или безопасный API доступ через уже настроенную среду; не запрашивать секреты в чате. OAuth repair остаётся отдельно.

**Дальше:** извлечь 37 строк GitHub leads, привести к источникам публикаций (многие являются ссылками на конкретные статьи/компании, не фидами); построчно добавить только пригодные и неповторяющиеся. EDA 65 сохранить как entity/event discovery leads, не выдавая их за подключённые источники. Не закрывать SD-001 до согласования live Inoreader evidence.

## SD-001 reconciliation result — 2026-10-24 (GitHub Austria/Belgium × Notion registry × EDA/ENDR)

**Read-only сравнение:** GitHub [country-source-leads-batch01-20261010.csv](https://github.com/nab0k/bohun-the-field/blob/main/docs/sources/country-source-leads-batch01-20261010.csv) содержит **37 rows / 37 distinct hostname**: Austria 27, Belgium 10. Сопоставление с [Notion Source Registry](https://app.notion.com/p/ce41b2f029ef4794853f1448ca33112b) (111 строк до EDA additions): **0 exact full URL**, **3 domain matches** (rheinmetall.com, leonardo.com, news.lockheedmartin.com), **34 distinct domains without match**. URL на статьи/карточки компаний не считать 34 новыми live publication channels; **37 rows remain candidate evidence leads**, без автоматического добавления как feeds.

**EDA/ENDR:** [Notion Intelligence Network](https://app.notion.com/p/3f6c42d0b11b80e4a06edf6483ae6954) утверждает 65 directory leads = 38 associations + 10 exhibitions + 17 clusters; полный row-level CSV в текущем доступе **отсутствует**, поэтому **65/65 live cross-check NOT DONE**. Исторически подтверждённые именованные EDA-003 Agoria/BSDI, EDA-028 NIDV, EDA-036 SOFF, EDA-050 SKYWIN, EDA-062 AERA: **5/5 не найдены по имени в текущем 111-row Notion registry**, добавлены как candidate publication surfaces с provenance и статусом Unknown/Unverified. Из EDA-043 Eurosatory и EDA-045 MSPO оба уже присутствуют в Top100, **не добавлены повторно**. Это **7 именованных EDA entities проверены по именам**, не 65. BDSV и BDLI уже в GitHub web-monitor конфигурациях, не добавлять как EDA новые.

**Readback после добавления 5 EDA кандидатов:** **116 Notion rows / 113 distinct Website URLs**. Не считать эти 116 verified live feeds.

**Blocker:** оригинальный 65-row `bohundefence_eda_endr_candidates_20261011.csv` / audit CSV упомянут в Notion, но не приложен к текущей среде и не найден в GitHub по известному пути. Для полной EDA/ENDR сверки нужен исходный файл либо повторная поштучная экстракция из официальных каталогов. Не выдумывать оставшиеся 58 записей.

**Следующее действие SD-001:** получить/восстановить EDA 65-row source CSV, выполнить полную 65-row name+canonical URL reconciliation и затем текущую Inoreader subscription readback. OAuth/access constraint separately logged.

## SD-001 checkpoint — 2026-10-24, EDA/ENDR reconstructed CSV + Inoreader live access check

**CSV artifact generated:** `bohun_eda_endr_reconstructed_65_20261024.csv` (local conversation attachment, not committed to repo): **65 rows**, 38 associations from EDA May 2024 PDF, 10 exhibitions from EDA 2026 annex, 17 selected ENDR clusters from European Commission directory. 29 country/region codes incl EU. **NOT the original 2026-10-11 EDA-001..065 file**; selected ENDR names may differ. Every row explicitly marked directory_named_only and ingestion_status=not_verified. Original PDF: https://eda.europa.eu/docs/default-source/documents/asd_ndias.pdf ; exhibitions: https://eda.europa.eu/docs/default-source/applicants/annex-2---major-exhibitions-2026.pdf ; ENDR: https://defence-industry-space.ec.europa.eu/eu-defence-industry/european-network-defence-related-regions/endr-members_en . CSV is research artifact, not feed import. No further Notion rows inserted in this pass.

**Inoreader current-state check:** no Inoreader provider connector available via plugin search; connected browser open-tabs list (read-only) contains no Inoreader tab. No authenticated live subscription API/list accessible via these paths. Therefore **current subscriptions, categories, unread counts, plan and last sync UNKNOWN**. Historical 40, 42, 67 counts are snapshots, not live. User should provide current OPML export or open Inoreader in connected browser / establish authorized read-only connection. Do not request or expose API secrets. This is an access blocker, not proof of no subscriptions.

**Caveat:** ENDR current official page shows 134 entities in prose while overview retains 132, compared with historical 132; do not interpret this discrepancy as source disappearance or subscribe 134 feeds.

## SD-001 — Inoreader connected browser live UI readback (2026-10-24)

**Evidence:** connected Browser tab `https://www.inoreader.com/search/feeds/Bundeswehr`, authenticated account sidebar, read-only page context. **16 named folders** visible, incl BOHUN_01_DEFENCE_MEDIA, BOHUN_02_INDUSTRY, BOHUN_03_GOVERNMENT, BOHUN_04_PROCUREMENT, BOHUN_08_INVESTORS_VC, BOHUN_09_STARTUPS, BOHUN_13_UKRAINE, BOHUN_MEDIA, BOHUN_INDUSTRY, BOHUN_OPPORTUNITIES, BOHUN_GOV, BOHUN_RESEARCH, plus Media & Analysis, Government & Procurement, Industry & Companies, Capital Aid & Events. **43 visible feed link occurrences**, **35 unique feed URLs** after dedup (8 repeated across folders). **All 35 visible unique feed domains match a domain in the current Notion 116-row registry**. This is a **lower bound on currently visible subscribed feeds**, NOT total subscriptions; sidebar may be partially loaded or folders collapsed. UI shows **4014 unread articles**, NOT subscriptions. No observed EDA named candidate feed (Agoria, NIDV, SOFF, SKYWIN, AERA, Eurosatory, MSPO, BDSV, BDLI) among 35 visible URLs. This is **not evidence of their absence from full account**.

**Next:** obtain full authenticated OPML export or read-only subscriptions API list for exact count and all-folder mapping; do not use historical 40/42/67 as live. No changes made to Inoreader subscriptions.

## SD-001 — Full Inoreader OPML reconciliation, 2026-10-24

**Evidence:** user-provided `Inoreader Feeds 20261024.xml`, valid OPML. Parsed **16 folders, 84 folder-feed memberships, 68 distinct xmlUrl**, 16 repeat memberships (same feed in two folders). This supersedes sidebar partial readback 35. OPML proves export inclusion at export time, not RSS endpoint health.

**Compared with current Notion Source Registry (116 rows, read 100 + 16 via SQL OFFSET):** by exact normalized **host** of OPML `htmlUrl` or `xmlUrl` vs Notion `Website`, **50/68 distinct feeds matched**, **18/68 feed domains absent**. The 50 matched feeds map to **39 distinct Notion source names** (one publisher can have multiple feed URLs, notably Defense News). 18 unmatched: C4ISRNET, Army Technology, Airforce Technology, Naval Technology, European Defence Review, Defence Industry Europe, Defence24, Tectonic, Ukrinform, Department of War News Feed (war.gov), Navy Lookout, DRONES (drones-magazin.de), Militär Aktuell, INDUSTRIEMAGAZIN, OIP Sensor Systems, Msm.sk, International Trade Administration (trade.gov), govinfo. **This is domain-based matching**, not proof that the precise Notion URL is the same RSS or healthy. Domain aliases may affect counts.

**EDA/ENDR specifically:** Agoria RSS `https://www.agoria.be/en/rss.xml` is present in OPML (matches Notion Agoria/BSDI); Targi Kielce RSS `https://www.targikielce.pl/feed` is present (matches Notion MSPO event publisher). NIDV, SOFF, SKYWIN, AERA not found by their corresponding domains in OPML. **BDSV/BDLI absent** from OPML by domain, despite GitHub web-monitor candidates. No feed additions made.

**Caution:** Ukrinform low-priority generic news; do not upgrade priority based on subscription. 18 missing domains are real Inoreader subscriptions absent from registry, not 18 verified high-value publisher candidates. Reconcile naming and source-type before adding; ensure canonical RSS URL stored separately from website, and distinguish channel connection from website candidate.

## SD-001 — Inoreader OPML gap closure, 2026-10-24

**Action authorized:** user requested to continue after OPML comparison. Added **18 previously missing-by-domain sources** as individual pages in [Notion Source Registry](https://app.notion.com/p/ce41b2f029ef4794853f1448ca33112b), using the user-provided `Inoreader Feeds 20261024.xml`. Stored exact OPML `xmlUrl` in Notes, publisher `htmlUrl` in Website, `Origin=Inoreader OPML export 2026-10-24`, `Connection=Confirmed current` meaning only OPML membership, `Content access=RSS candidate`, checked date 2026-10-24. Ukrinform marked Low candidate; other sources Unrated. Country Unverified pending research. 18 names: C4ISRNET, Army Technology, Airforce Technology, Naval Technology, European Defence Review, Defence Industry Europe, Defence24, Tectonic, Ukrinform, Department of War News Feed, Navy Lookout, DRONES, Militär Aktuell, INDUSTRIEMAGAZIN, OIP Sensor Systems, Msm.sk, International Trade Administration, govinfo.

**Readback:** Notion SQL `COUNT(*)=134`, `COUNT(DISTINCT Website)=131`, `COUNT(Origin='Inoreader OPML export 2026-10-24')=18`. Pre-action 116/113. **All 68 unique OPML feed URLs now have a matching publisher domain in Notion**, assuming same canonical host logic. No RSS HTTP health check, no Supabase ingest confirmation, no subscription changes, no production deployment. **Next:** per-feed XML HTTP health/freshness test and pipeline ingress check, plus full EDA/ENDR 65-row provenance/dedup, without conflating candidate with operational feed.

## SD-001 — Supabase ingestion audit, 2026-10-24 (read-only)

**Verified project:** Supabase `tecrucawzukxqtchpxrh` / `bohundefence-intelligence` status ACTIVE_HEALTHY. Read-only SQL against `intelligence.sources` and `intelligence.items`.

**Items:** 5,595 total; 293 with `discovered_at >= now()-24h`; max discovered_at `2026-10-24 11:17:32 UTC`. By source_id: Inoreader BOHUN `id=2`, active=true, collection_method=official_api, **5,372 total / 293 last 24h**; TED `id=1`, **223 total / 0 last 24h**, latest `2026-10-23 12:38:05 UTC`; BORME `id=3`, **0 total**. Do not confuse `sources` rows (3 ingestion connectors) with 68 subscribed RSS feeds.

**Inoreader origin metadata:** raw_data contains `origin_stream_id`, `origin_title`, `folder`, `inoreader_id`, `published`. In Inoreader ingested records, **58 distinct origin_stream_id**, **48 distinct origin_title** historically; **42 distinct origin_stream_id / 33 origin_title** seen with discovered_at in last 24h. Compare to user OPML **68 unique xmlUrl**; cannot conclude 10 feed failures without URL normalization and lookback/publish-frequency checks. Agoria specifically present as `feed/https://www.agoria.be/en/rss.xml` with **136 ingested records**, latest discovered 2026-10-23 21:20:37 UTC. Top publishers by ingested count: Ukrinform 1015 (generic, low priority), Defense News 401, Tech.eu 287, UK MOD Activity 285, Defence Industry Europe 261, Targi Kielce 254. This proves actual pipeline ingestion for these sources, not merely OPML subscription.

**Next:** exact normalized OPML xmlUrl ↔ Supabase raw_data.origin_stream_id 68-way reconciliation, identify never-ingested and stale streams, then RSS HTTP/feed freshness checks; protect distinction between sparse low-frequency publication and broken feed. No production writes.

## Ближайший milestone

**M1 — инвентаризация:** список существующих источников, формат доступа, страна, категория, статус проверки, принадлежность к Inoreader, источник доказательства и дата проверки.

**M2 — карта пробелов:** измеренное покрытие и приоритеты поиска.

**M3 — проверенный каталог:** источники с URL, классификацией, доступностью, качеством, способом подключения и историей проверок.

**Финиш Source Discovery:** согласованный каталог с прозрачным покрытием и воспроизводимым процессом добавления новых источников. Полная глобальная исчерпываемость не предполагается.

## Правила ведения

1. При каждом изменении задачи добавлять датированную запись: что сделано, доказательство, ограничение, следующий шаг.
2. Не ставить DONE без проверяемого результата.
3. Не подменять задачу поиска источников технической интеграцией: технические проблемы записывать как зависимые подзадачи.
4. Не переносить устаревшие количества из разговоров в актуальные метрики без сверки.
5. Изменения основной ветки, базы, Notion, production и публикации согласовывать отдельно.
