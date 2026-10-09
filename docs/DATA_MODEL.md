# Модель данных THE FIELD v0.1

Схема: `db/schema.sql` (Cloudflare D1 / SQLite, 22 таблицы). Проверена загрузкой в SQLite 09.10.2026.

## Поток данных

```
источник ─► raw_item (как есть, неизменяемо)
              │  нормализация: правила, затем AI только где нужно
              ▼
           entity + детали по типу  ◄── fact (значение + цитата + источник)
              │  гейты: candidate → verified → qa_passed → published
              ▼
           public_entity ─► карта, поиск, вид страны, Newsfeed
```

## Таблицы

| Группа | Таблицы | Зачем |
|---|---|---|
| Справочники | `country`, `category`, `source` | Страны с допуском (eligible / review / excluded), дерево категорий и уровней supply chain, источники с лицензией |
| Сырые данные | `raw_item` | Ответ источника как есть. Уникальность `(source, external_id, hash)` даёт идемпотентный импорт и историю версий |
| Сущности | `entity`, `entity_alias`, `entity_category`, `entity_country`, `entity_relation` | Всё, что на карте: одна таблица, один поиск, один вид страны. Связи: дочерняя компания, инвестор, организатор, заказчик |
| Детали | `organization`, `public_person`, `event`, `funding_round`, `opportunity`, `program`, `policy` | Поля, специфичные для типа |
| Newsfeed | `news_item`, `news_entity` | Заголовок + ссылка, слои и страна, кластер одной истории |
| Происхождение | `fact` | Каждое извлечённое значение с цитатой и способом извлечения (rule / llm / human) |
| Служебное | `audit_log`, `job_run`, `llm_cache` | История изменений, мониторинг сборщиков, кэш AI |

## Как ложатся существующие данные

| Что есть | Куда |
|---|---|
| 89 тендеров TED (Notion, `tenders.sqlite3`) | `raw_item` (source `ted`) → `entity(kind=opportunity)` + `opportunity`, статус `candidate` |
| 1 017 публикаций Inoreader (`collector.sqlite3`) | `raw_item` (source `inoreader:…`) → `news_item` |
| 108 сигналов Intelligence v2 | `news_item` с `layers`, либо `entity`, если сигнал — компания/раунд; решить после просмотра |
| 266 Opportunities v3 | `entity(kind=opportunity)`, дедуп с TED по `source_notice_id` |
| Компании Classic №3, research HQ C01–C35 | `entity(kind=organization)` + `organization`, флаг `china_controlled` в `flags` |

## Переход на PostgreSQL (если понадобится)

TEXT-даты → `timestamptz`, TEXT-JSON → `jsonb`, `INTEGER PRIMARY KEY` → `bigint generated always as identity`, частичные индексы переносятся как есть. Логика и названия не меняются.
