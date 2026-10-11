# Журнал архитектурных решений

Один источник правды для всех исполнителей (Claude, ChatGPT, Codex). Новое решение — новая запись внизу; старые не переписываются, а помечаются «заменено записью N». Если код или чат противоречит этому файлу, прав этот файл, пока Сергій не решил иначе.

---

### 001. Сайт — карта THE FIELD на bohundefence.com (09.10.2026, Сергій)
Вся база об отрасли живёт на .com. Слои: Industry, Supply chain, Capital, Events, Policy, Opportunities, Associations, Infrastructure, Live + Newsfeed и открытый раздел «Украина». Подробно: Notion, «Структура сайта bohundefence.com (решения 09.10.2026)».

### 002. Инфраструктура: GitHub + Cloudflare, бесплатные тарифы (09.10.2026, Сергій)
- GitHub — код и справочные данные; правки через коммиты.
- Cloudflare Pages — хостинг сайта (работает и с приватными репозиториями).
- Cloudflare Workers + Cron — сборщики, прокси к API, ключи в секретах.
- Cloudflare D1 — база (SQLite). Схема: `db/schema.sql`.
- GitHub Actions — медленные задачи по расписанию (раз в час/сутки), если удобнее, чем Worker.

Заменяет совет из чата от 09.10 про Supabase + GitHub Pages. Причина: решение 002 уже принято и тоже бесплатно; D1 рядом с Worker, без внешнего сервиса.

### 003. Единая модель данных (09.10.2026, Claude)
Одна таблица `entity` для всего, что на карте и в поиске (организации, события, раунды, тендеры, программы, регулирование) + таблицы деталей по типу. Новости отдельно (`news_item`), связаны с сущностями и странами. Сырые данные (`raw_item`) неизменяемы. Подробно: `docs/DATA_MODEL.md`.

### 004. Факты только с источником (09.10.2026, Claude, по ТЗ)
Суммы, бюджеты, дедлайны, компании, извлечённые кодом или AI, пишутся в `fact` с короткой цитатой из первоисточника. Код проверяет, что цитата действительно есть в тексте (`evidence_verified`). Непроверенный факт на сайт не выходит.

### 005. Гейты публикации (08.10.2026, QA & Handoff)
`candidate → verified → qa_passed → published`, плюс `rejected` и `unresolved`. Сайт читает только `published` (вью `public_entity`). «Найдено» не равно «подтверждено».

### 006. Украина: без адресов, координат предприятий и людей (08–09.10.2026, Сергій)
Закреплено в схеме: CHECK запрещает координаты для `country_iso2 = 'UA'`; `public_person` и `hq_address` для Украины не заполняются (проверка в коде импорта).

### 007. Новости: только заголовок, источник, дата, ссылка (09.10.2026)
Полный текст не храним и не показываем. Сайт читает только папки Inoreader с префиксом `BOHUN_`.

### 008. Тендер без дедлайна — не «открытый» (09.10.2026, Claude)
`deadline_at = NULL` означает «неизвестно». Статус `open` ставится только при будущем дедлайне из первоисточника и типе объявления «конкурс». Объявления о присуждении контракта и предварительные уведомления — отдельные типы.

### 009. Inoreader — один из входов, не центр (09.10.2026, Claude)
Каноническая запись всегда в D1. Inoreader доставляет новости; RSS без Inoreader читается напрямую. Зависимость от Inoreader ограничена слоем Newsfeed.

### 010. Ключи (09.10.2026)
Ключи только в `.env.local` (в `.gitignore`) и в секретах Cloudflare/GitHub. Не в Git, Notion, логах и чатах. Notion-токен, засвеченный ранее, отозвать после запуска (решение Сергія: сначала строим).

### 011. Сигналы вместо ленты, публикация без человека (09.10.2026, Сергій)
На сайт выводятся не чужие заголовки, а сигналы: события, собранные из нескольких публикаций, пересказанные ИИ своими словами и отмеченные на карте. Публикация автоматическая, без ручной проверки, в реальном времени (опрос каждые 15 минут). Все ограничения, которые раньше обеспечивал редактор, записаны в `docs/AI_EDITORIAL_RULES.md` и исполняются кодом. Решение 007 (только заголовок и ссылка) остаётся в силе для источников: полные тексты не публикуются, ссылки на источники в каждой карточке. Заменяет предложение Claude о проверке каждого сигнала человеком.


### 012. Supabase canonical; Cloudflare delivery (11.10.2026, owner-approved)
Status: APPROVED architecture direction only. Supersedes ADR 002 and ADR 009 solely regarding canonical database choice; preserve their historical text.

- Supabase PostgreSQL, existing intelligence schema, is the sole operational write-master for ingested records, source evidence, versioned industrial knowledge, relationships, indicators and QA decisions.
- GitHub Actions remains collection and controlled Gemini analysis infrastructure. AI interpretations do not become verified knowledge automatically.
- Cloudflare Workers/Pages serves read-only approved API, website and existing Claude MapLibre atlas.
- Cloudflare D1 may be proposed later solely as an optional one-way, derived, public-data cache, subject to proven need and separate owner authorization. Never an independent canonical writer.
- Notion remains research, methodology and editorial coordination; any subsequent import retains provenance and review history.

This ADR authorizes documentation and read-only inspection only. It does NOT authorize schema or data changes, migrations, Cloudflare resources, production deploy, publishing, secrets changes or paid usage. Further execution requires explicit owner authorization. QA remains mandatory for unverified model outputs.

Specification: https://app.notion.com/p/3f6c42d0b11b814bb87cd7bf1a47bd23
