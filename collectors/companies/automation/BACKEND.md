# Общий backend компаний: подготовленный кандидат

02.11.2026. Изолированный пакет EXP-016, не установленная система.
18 файлов прежнего ядра сохранены побайтно. Добавлены адаптер, кодек, этот документ,
SQL-кандидат и три файла тестов. Нет сетевого клиента, драйвера БД, модели или
расписания. Оригинальные библиотеки извлечения и сопоставления подключаются позже
через уже согласованные адаптеры; их выполнение этим пакетом не проверяется.

## Механика и интерфейсы

`PostgresBackend(rpc, policy=..., clock=..., card_factory=..., triggers=())`
получает доверенный транспорт `rpc(action, request)` извне. Запрос содержит ровно
`wire_version=1`, `policy_hash`, `args`; ответ — `wire_version=1`, `status=OK`, `data`.
Транспорт должен применять лимит до разбора JSON, отклонять дубли ключей, использовать
UTC и преобразовывать только известные безопасные ошибки SQLSTATE/detail в
`BackendFault`. Сырые ошибки, тела источников и секреты в журнал не передаются.

QueuePort использует claim/context/read_prepared/save_prepared/commit_result/defer/
lookup_result, плюс renew. Immutable prepared сохраняется перед commit. Потерянный
ответ записи не означает её отмену: адаптер ищет квитанцию; при отсутствии
подтверждения возвращается UNKNOWN, без повторной записи или модели.

A26 использует read_current/commit_batch. Фабрика CardRead внедряется из A26, без
импорта его приватной локальной папки. Полные связанные записи и authoritative
manual ownership обязательны. Для новых seen_triggers конструктору нужны исходные
`trigger_receipt(company_id, origin_id, content_hash, sections)` из допущенного
источника. Один hash из prepared не доказывает поступление триггера.
Это протокольная совместимость; интеграция class identity реального A26 ещё не проверена.

Полный реестр читается через read_registry_page(limit<=100). plan_registry_page
атомарно сохраняет jobs и позицию. Новый клиент читает сохранённую позицию, а не
начинает с первой компании. После последней порции курсор сбрасывается; revision
обхода отличает новый цикл от старой квитанции. Изменённая после чтения порция
отклоняется локально; сервер заново проверяет порядок, область и revision.
Пустой реестр читается без записи. Удаление всех оставшихся компаний при уже
сохранённом ненулевом курсоре требует отдельного разбора; клиент не сбрасывает
такую позицию самовольно.

Кодек checkpoint сохраняет типы старого ядра, tuple/set в явном JSON, строгие
company/section записи, ISO UTC и непрозрачные исторические ключи. Ограничения:
4 MiB/24 уровня, A26 <=10 компаний, registry <=100, <=5000 job keys, <=500 triggers,
<=1000 section-map записей. Превышение — отказ, без обрезания истории. Это предел
одной порции, не всей базы. Нужна будущая проверка роста истории.

## SQL — только проект для просмотра

`db/companies/automation_v0_candidate.sql` ни разу не выполнялся. Предлагает три
закрытые служебные таблицы: automation_job, automation_control и
automation_checkpoint; существующие company/profile_source/profile_claim/
profile_claim_source/g4_section_review/observation переиспользуются.
Control по умолчанию disabled/dry_run, пустая область. Код не может включить себя.
RPC проверяет private direct DB caller, policy, scope, lease и current base.
NOLOGIN роли не дают способа подключения; LOGIN/секреты/транспорт — отдельная задача.
SECURITY DEFINER имеет пустой search_path, полные имена и REVOKE PUBLIC.
Реальная применимость grants/RLS и создание функций пока не проверены.

Будущий транспорт должен начать короткую SERIALIZABLE транзакцию для операций
записи и worker; read_current/read_registry_page/lookup допускают read-only
REPEATABLE READ. Нельзя считать завершение RPC подтверждением COMMIT: транспорт
возвращает успех только после COMMIT. При потере связи или serialization/deadlock
нужна явная классификация результата; адаптер не делает слепого повтора.
Внутри транзакции нет HTTP, Notion или модели.

Два обязательных доверенных SQL-hook здесь намеренно **не создаются**:

- automation_profile_read_v0(text[]) возвращает source_revision, cards, contexts,
  section_states и при наличии проверенный research_admitted. Он должен строиться
  на A26, связанных данных, источниках и явном владельце поля. read_at — время
  чтения, не часть устойчивого source_revision. Одна company row не полный профиль.
- automation_source_read_v0(text,text,text,text) проверяет company/source_ref/url/hash
  по сохранённому источнику и возвращает admitted/text_hash/binding и перечень
  независимо допущенных verified_content_hashes. Доверие не берётся из payload
  работника. Существующая profile_source запись обязательна; этот пакет её не создаёт.

Без hooks PROFILE_READER_NOT_READY/SOURCE_READER_NOT_READY. Новые claims — HOLD,
public_release=false; rejected содержание и ручные/непустые поля защищены.
Fill только country/website, при явном machine owner и проверенном supporting hash.
Отсутствие owner не разрешает fill. Чувствительная украинская география закрыта.
Has-approved coverage сохраняется. Отрицательный результат требует research receipt.

## Что доказано и что остаётся

Стандартные offline-тесты проверяют исходное ядро, wire roundtrip, контракт порта,
потерянные ответы, конфликты, повторные обходы и fake-хранилище. Fake реализует
ожидаемый протокол независимо от SQL: это **не** PostgreSQL, не проверка RLS/locks,
не доказательство SQL-синтаксиса или конкурентных транзакций.

Перед установкой нужны свежие schema-only metadata, просмотр роли/SQL и отдельно
разрешённая disposable PostgreSQL проба: миграция, canonical hashes (включая Unicode/
timestamps), две сессии claim/CAS/stop, rollback/unknown, RLS/EXECUTE, cross-company/
manual/rejected/UA/quote negatives. Нельзя переносить результат fake-тестов на БД.
После этого — совместный реальный reader, native extraction/source admission,
private dry-run 30+10, отдельные write pilot/расписание/публикация.
