# HANDOFF — BOHUN / THE FIELD (bohundefence.com), передача от Claude, 11.10.2026

Ветка `claude-complete-handoff` = локальный `main` проекта `/Users/serhiinabok/Projects/bohundefence-field`
на коммите `04c179e` + снимок всей незакоммиченной работы (`e404618`) + внешние материалы сайта (`36d6b45`) + этот файл.
Ничего не удалено и не переписано. `main` на GitHub не тронут.

## 0. Главное за минуту

- **Главный продукт сейчас — `atlas.html`**: глобус (MapLibre) с данными об оборонной отрасли. По решению Сергія
  от 09.10.2026 сайт bohundefence.com становится картой всей оборонной отрасли (компании, цепочки поставок,
  события, фонды, регулирование, новости, живой транспорт) плюс отдельный раздел Украины. Об услугах Bohun —
  короткое приветствие и одна страница.
- **`field.html` (v0.4)** — предыдущий прототип: игровая сцена Phaser «Потоки» на реальной географии Украины и
  Европы. Остановлен; его механика (`src/flows/core.js`) переиспользуется в atlas для демо-маршрутов.
- Решения и структура сайта — в Notion (рабочая страница «BOHUN / THE FIELD — рабочий план и роли (с 08.10.2026)»):
  «Структура сайта bohundefence.com (решения 09.10.2026)», «Стратегия Сергія (09.10.2026)»,
  «HANDOFF Claude → ChatGPT / Codex: карта bohundefence (10.10.2026)».

## 1. Запуск

```bash
npm install
npm run dev          # http://localhost:5174  (atlas: /atlas.html, field: /field.html, flows: /flows.html, game: /game.html)
npm test             # 61 проверка, все проходят на 04c179e
npm run build        # сборка в dist/ (прокси /live/* в сборке НЕ работают, см. §6)
```

- Node 22+ (использовался Node 26). Секреты — только в `.env.local` (в `.gitignore`, в репозитории его нет):
  `INOREADER_APP_ID`, `INOREADER_APP_KEY`, `INOREADER_ACCESS_TOKEN`, `INOREADER_REFRESH_TOKEN`,
  `INOREADER_TOKEN_EXPIRES`, `AISSTREAM_API_KEY`. Без `AISSTREAM_API_KEY` суда по миру не появятся (Балтика из
  Digitraffic останется). Ключи у Сергія; ранее часть ключей попадала в переписку — их рекомендовано ротировать.
- Полезные параметры atlas: `?look=relief|hills|parchment|dark|light`, `?night=0`, `?live=1`, `?demo=scale`,
  `?journeys=1`, `?industry=1`, `?sea=1`, `?projection=mercator`, `?vehicles=1`.

## 2. Страницы

| Страница | Что это | Статус |
|---|---|---|
| **atlas.html** | Глобус MapLibre + слои данных. Главный продукт. | **Активная разработка**, всё в §3 |
| **field.html** | v0.4: вид среза v0.3 + механика «Потоки» на реальной географии (Phaser 3.90). Русский интерфейс. | Работает, заморожен |
| **flows.html** | Прототип механики «Потоки»: перевозчики, маршруты (быстро / дёшево / без воздуха), доставки, монеты банка. | Работает; в рабочей копии есть **незакоммиченные чужие правки** `src/flows/main.js` и `public/flows/world-tilt-base.svg` (+ `.bak`/`.bohun-backup`) — сохранены как есть в `e404618` |
| **game.html** | Старый игровой модуль: сцена по контракту v0.2 (коническая проекция, `world-layout.json`), временная графика. | Работает, устарел |
| **index.html** | Самый ранний срез: Presentation Mode, I NEED / I HAVE, PROPULSION-досье, Game Mode (`src/main.js`, `world.js`, `scene.js`, `art.js`). | Работает, устарел |

## 3. atlas.html — что сделано

Файлы: `atlas.html`, `src/atlas/main.js` (сборка всего), `src/atlas/atlas.css`.

| Блок | Файл | Состояние |
|---|---|---|
| Карта: MapLibre 6 глобус, подложки OpenFreeMap, рельеф AWS Terrain Tiles, свои границы Natural Earth (Крым в составе Украины, `scripts/build-atlas-borders.mjs` → `public/atlas/countries.geojson`) | `main.js` `loadStyle()` | **Работает** |
| Воркер MapLibre: дословная копия в `public/atlas/vendor/` + `setWorkerUrl` (иначе под Vite dev воркер не отвечает) | `scripts/copy-maplibre-worker.mjs` | Работает, тест сверяет версию |
| Ночная сторона Земли: NASA Black Marble (GIBS), терминатор по реальному времени, обновление раз в 5 мин | `src/atlas/night.js` | **Работает**; баг: светлое пятно у северного полюса |
| Подписи: 202 столицы (Natural Earth) + 24 областных центра Украины + Симферополь, украинская транслитерация | `scripts/build-places.mjs` → `public/atlas/places.json` | **Работает** |
| **Компании Австрии: 50** из Notion DB «Companies (all countries)» (accepted + Publishable), точка = центр города (Nominatim, кэш) | `data/notion/companies-AT.json` → `scripts/build-companies.mjs AT` → `public/atlas/companies-at.json` | **Работает**, реальные данные |
| Уровни детализации: мир — страны закрашены по числу; регион — кластеры с кольцом по категориям; город/близко — точки и названия | `src/atlas/scale-demo.js` (`createScaleDemo`) | **Работает** |
| Удобство: левая панель (поиск `/`, слои, категории-чипы: клик вкл/выкл, двойной — только эта), правая панель (World › страна, сводка по категориям, список, карточка с «← Back to list», `Esc`), подсказки при наведении, золотое кольцо выбора, клик по стране — вид страны | `main.js` (поиск, слои), `scale-demo.js` (панель) | **Работает**; наведение проверено только кодом |
| Демо масштаба: 30 000 сгенерированных точек (не компании) | `scale-demo.js` `generate()`, `?demo=scale` | Эксперимент, работает |
| LIVE: самолёты adsb.lol (ODbL), военные выделены (`/v2/mil`, весь мир); суда AISStream (через dev-ретранслятор) + Fintraffic Digitraffic (Балтика); поезда Digitraffic (Финляндия). Ничего над Украиной (+~30 км) и Чёрным/Азовским морями | `src/atlas/live.js`, `dev/aisstream-relay.mjs`, прокси в `vite.config.js` | **Работает только в `npm run dev`**; задержки показа нет (будет в Worker) |
| Демо-маршруты SELL / BUY + всадник Богун, механика `flows/core.js` | `src/atlas/journeys.js`, `main.js` | **Скрыто** (`?journeys=1`): заглушка-демонстрация, вымышленные участники |
| Старый слой Industry (72 компании пилота DE/FR/IT/PL/CZ из Wikidata + 17 названий Украины без мест) | `scripts/build-industry.mjs` → `public/atlas/industry.json` | **Скрыто** (`?industry=1`); сырой пул Wikidata был во временной папке и потерян — `industry.json` сохранён |
| Подводная инфраструктура: 44 + 3 компании из статьи Сергія (LinkedIn, 02.01.2026) | `scripts/build-undersea.mjs` → `public/atlas/undersea.json` | **Скрыто** (`?sea=1`) |
| 3D-модели Kenney (CC0) | `public/atlas/models/**`, `scripts/fix-kenney-glb.mjs` | Используются только для `?vehicles=1`; здания и изометрические объекты **убраны** по решению Сергія |

Архитектура фронтенда: Vite 8, без фреймворка (ванильный JS, ES-модули с top-level await). Карта — MapLibre GL 6;
deck.gl 9.4 (`MapLibreOverlay` interleaved) только для линий маршрутов и 3D; всё, что должно правильно стоять на
глобусе (точки, подписи, значки), — слои самой MapLibre или HTML-маркеры (иконки deck.gl на глобусе растягиваются).
Phaser 3.90 — field/flows/game. Данные — статические JSON в `public/`, собираются скриптами из `scripts/`.
Тесты — `scripts/test-*.mjs` (node:assert), в том числе правила: Крым в Украине, ничего живого над Украиной и
Чёрным морем, нет компаний РФ/Беларуси/КНДР/Китая в тестовых данных, нет ссылок на Notion в публичных файлах.

## 4. Что имитируется (не реальные данные)

- Демо-маршруты, участники, «предложения», «эксперты», доставки и монеты (atlas `?journeys=1`, field, flows, game).
- 30 000 точек демо масштаба.
- Ссылки «Contact», формы — не подключены.
- Newsfeed на сайте ещё нет (только план и Inoreader-подготовка).

## 5. Правила и решения Сергія (нельзя терять)

- Украина: только названия компаний из открытых источников; без мест, адресов и людей. Не наносить компании РФ,
  Беларуси, КНДР, Китая. Компании под контролем китайских групп — показывать **с пометкой** (после санкционной проверки).
- Только открытые источники; «Open data. Not clients or partners of Bohun.»; на сайте нет ссылок на Notion.
- Публичные руководители и адреса штаб-квартир — только из публичных источников (кроме Украины).
- Транспорт: только бесплатные API с лицензией для коммерческого сайта (OpenSky, adsb.fi — нельзя); ничего над
  Украиной и Чёрным морем; с задержкой (на сервере).
- Игра (будущий раздел Украины): ничего не стреляет и не разрушается; статичные картинки с обновлением из API,
  движутся границы, закраска как в источнике (DeepStateMap — Сергій запросил API; искать альтернативы).
- Вся база об отрасли — на **.com**. Инфраструктура по возможности бесплатная: GitHub + Cloudflare (Pages, Workers, D1/KV).
- Изометрические объекты и 3D-здания — убраны; на карте только точки, пока нет данных — нет точек.
- Ничего не публиковать и не деплоить без прямой команды Сергія. С Сергієм — по-русски.

## 6. Известные баги и ограничения

- Прокси `/live/*` и ретранслятор AISStream есть только в dev-сервере Vite; в `npm run build` слой LIVE не получит данных.
- adsb.lol иногда отвечает 429 — слой ждёт и удваивает интервал; гражданские самолёты видны только с масштаба 4.5.
- Ночной режим: светлое пятно у северного полюса.
- Узкий экран: категории в левой панели скрыты; мобильной «шторки» нет.
- Надпись EUROPEAN UNION ложится на Австрию при включённых компаниях.
- `scripts/build-industry.mjs` ждёт сырой пул Wikidata из временной папки, которой больше нет (пересобрать запросом).
- Во встроенной панели браузера Claude карта рендерится с задержкой — это не баг сайта.

## 7. Обсуждено, но не сделано

1. Вторая очередь удобства: ссылка на текущий вид (URL), чистая подложка при включённых компаниях, плоская карта
   по умолчанию + кнопка 3D, дата данных в панели, мобильная шторка.
2. Скрипт, который сам читает Notion DB «Companies» (нужен ключ интеграции Notion в `.env.local`) и собирает все
   страны; далее Бельгия, Нидерланды, Германия.
3. Слои из структуры сайта: Supply chain (дерево изделие → компоненты → кто делает), Capital (фонды, раунды),
   Events (выставки, календарь), Policy (страны по бюджету, экспортному контролю, соглашениям с Украиной),
   Opportunities (тендеры TED/NSPA — после юридической проверки формулировок), Associations, Infrastructure.
4. Newsfeed везде на сайте: Inoreader (папки `BOHUN_…`) → Cloudflare Worker → заголовок, источник, дата, ссылка
   (без полного текста), разметка по слою и стране. Набор проверенных лент: `data/inoreader/bohun-feeds.opml`;
   аудит источников другой сессии: `data/inoreader/source_audit_report.csv`, `bohun_*`, `inoreader-folder-plan.json`.
5. Раздел Украины (открыт всем): карта фронта (DeepState или альтернатива), программы помощи, коалиции, новости.
6. Страница «Что делает Bohun» (I NEED / I HAVE) — туда переезжают демо-маршруты.
7. Cloudflare Worker: прокси `/live/*`, AISStream, задержка транспорта, Inoreader; хостинг на Pages.
8. Открытые вопросы Сергію: показывать ли Москву / Минск / Пекин / Пхеньян как столицы; Севастополь.

## 8. Следующий шаг (рекомендация)

1. Решить, как свести эту ветку с `origin/main`: там 96 более новых коммитов другой среды (коллекторы, ADR 012
   Supabase, OpenAlex, тесты, `vite.site.config.js`); в этой ветке 8 коммитов atlas, которых нет в `main`
   (`67cbb67`…`04c179e`) + снимок. Конфликты возможны в `atlas.html`, `src/atlas/*`, `vite.config.js`, `package.json`.
   Ничего не переписывать — сливать через PR.
2. Перенести LIVE-прокси и AISStream в Cloudflare Worker, затем публиковать atlas.
3. Автоматическая выгрузка компаний из Notion по всем странам.

## 9. Где что лежит (карта файлов)

- Страницы: `atlas.html`, `field.html`, `flows.html`, `game.html`, `index.html`; конфиг `vite.config.js`.
- `src/atlas/` — глобус: `main.js`, `scale-demo.js` (слой данных и панели), `live.js`, `night.js`, `journeys.js`, `atlas.css`.
- `src/field/` — v0.4 (`terrain.js`, `scenarios.js`); `src/flows/` — механика «Потоки» (`core.js` без фреймворка, `data/`);
  `src/game/` — старый модуль (`core.js`, `geo.js`, `scenario.js`); `src/*.js` — ранний срез index.html.
- `public/atlas/` — данные atlas; `public/field/hero-geo.json`; `public/flows/world-tilt-base.svg`; `public/map/` — база
  сцены game; `public/assets/bogun/` — спрайт всадника Classic v1.
- `scripts/` — сборщики данных, тесты, Inoreader (`inoreader-auth.mjs`, `inoreader-organize.py`, `build-opml.mjs`).
- `dev/aisstream-relay.mjs` — dev-ретранслятор судов. `collectors/ted/`, `.github/workflows/ted-daily.yml`,
  `scripts/run_ted_daily.sh` — сбор тендеров TED (другая сессия).
- `data/notion/` — выгрузки из Notion и кэш геокодов; `data/inoreader/` — OPML и аудит лент; `data/sources/registry.csv`.
- `handoff/` — материалы Classic: `classic-tilt-v03/` (фон 45°, задания, сетки), `classic-probe1-result/` (результат
  пробы 1), `classic-bohun-v2/` (задание на спрайт Богуна v2), `classic-world-tiles-v1/`;
  `external-site-materials/` — срезы v0.2/v0.3 (база field.html), `bohun_playable_v01`, концепт-арт, GIF сцен.

## 10. Что НЕ в Git

- `.env.local` (секреты), `node_modules/`, `.venv/`, `dist/`, `.claude/`.
- `data/inoreader/private/` — резервная копия личных подписок Inoreader Сергія (личное, только локально).
- `~/Downloads/Bohundefence/Сайт/Assets/` (319 МБ сторонних наборов: Kenney, Slavic World, Natural Earth raster,
  Tiled) — список в `handoff/external-site-materials/ASSETS-NOT-IN-GIT.txt`.
- Локальные архивы (на компьютере Сергія, `~/Projects/bohundefence-backups/`):
  `bohundefence-field-full-2026-10-11.tar.gz` (весь проект с историей Git и всеми ветками, без секретов и зависимостей),
  `site-materials-Downloads-Sait-2026-10-11.tar.gz` (вся папка «Сайт», включая Assets).
- Две локальные ветки без пары на GitHub отправлены как `handoff/local-feature-signals-v1` и `handoff/local-publish`
  (Signals v1: AI-сигналы и слой SIGNALS — работа другой сессии, в эту ветку не сливалась).
