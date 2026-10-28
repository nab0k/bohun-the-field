# Offline source onboarding v1

Изолированный прототип G3. Старые collectors/runtime/SQL не импортируются и не вызываются.
Никаких HTTP, БД, Notion, моделей, checkpoint apply или автоматического допуска источников.
Стандартная библиотека Python3.13; дополнительных зависимостей нет.

Один master приоритетов — `SOURCE_CONNECTION_BRIEF_v0.md` в BOHUN-local.
CLI принимает его точный просмотренный SHA256, проверяет42строки/6колонок/ID/priority.
CODE/DOC/GATE и общий hostname не означают live readiness. Все readiness UNKNOWN.
Operational profiles отдельно: Grants.gov/S04 и OpenAlex/S34, только OFFLINE_ONLY.

Входы — JSON synthetic отчётов в неизменённых форматах существующих Grants/OpenAlex
collectors. Пределы:1MiB/input,500rows,64KiB/row. Missing/null/empty/invalid различимы.
Карантин и дубли отражаются по каждому input_index, конфликт key удерживает все версии.
Стабильный record_ref — namespace/native ID/semantic payload; это идентификатор кандидата,
не опубликованного события. Между запусками нет persistent store/event delivery.
Тот же вход воспроизводит те же candidates; изменение observed_at меняет receipt, не record_ref.
Discovery query/matched_keywords исключены из semantic revision; все их наблюдения
сохраняются в provenance.discovery_observations, в том числе при дедупликации.

SourceArtifact сохраняет scope/errors/coverage и producer identity. Legacy reports не
содержат исходных API bytes/точной upstream revision: соответствующие hashes пусты,
upstream UNKNOWN. Input JSON hash отдельно. Никакой metadata→full text конверсии.
Dates сохраняются raw с неизвестной точностью/зоной, статус/parent не угадываются.
Права, чувствительность, полнота и релевантность не подтверждены; QA HOLD/public_release=false.
Destination proposals всегда NO_WRITE/DESTINATION_NOT_ADMITTED; source_id2 не используется.

Выход: run_manifest.json и4хешируемых файла — registry_projection.json, records.jsonl,
audit_report.json (с полными envelopes), destination_proposals.jsonl. Manifest не хеширует
сам себя; его SHA256 возвращается отдельно. Нужны явные producer SHA/code-set hash:
никакого автоматического Git/env/credential discovery; dirty обозначается DIRTY_ISOLATED.
Эти supplied claims фиксируют provenance, но не являются подписью доверенного producer.

Публикация bundle только в новом прямом подкаталоге private root. Сначала bounded stage,
валидация, затем exclusive directory reservation и atomic no-overwrite hard links файлов,
manifest последним. Это уточнение файловой операции вместо заменяющего POSIX rename:
manifest — точка успешной финализации. Crash может оставить неполный каталог без manifest;
он отвергается и не перезаписывается. Старые файлы/state не затрагиваются. Symlink/traversal/
public/dist/repository output запрещены. Каталоги контролируются одним локальным владельцем;
защита от злонамеренной смены ancestor другим процессом с теми же правами не заявляется.
Fsync файлов не доказывает восстановление после потери питания всего диска.

CLI `scripts/source-connection-audit.py`: обязательны --master, --master-sha256, --grants,
--openalex, --producer-git-sha, --producer-code-hash, --observed-at (UTC Z), --output,
--synthetic. Output root — BOHUN-local/local-runtime/experiments/EXP-009/source-onboarding;
CLI не создаёт его автоматически. In-process tests передают разрешённый TemporaryDirectory.
Не направлять реальные/private/raw Notion данные в этот synthetic прототип.

Проверка: `/opt/homebrew/bin/python3.13 -B -m unittest discover -s tests/source_registry/onboarding -p 'test_*.py' -v`.
Suite блокирует network/DNS/HTTP/DB/subprocess до импорта пакета; fixtures искусственные.
Live/API completeness, source rights, target mapping/SQL и downstream integration NOT_RUN.
Это не EDF Collector v2 и не подключение42источников. Интеграция, реальные прогоны,
коммит/GitHub/Notion/публикация требуют отдельных решений.
