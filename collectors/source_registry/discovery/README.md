# BOHUN Source Discovery

Локально интегрированный пакет постоянного поиска новых источников. Реальные поисковые потоки, writer, БД и служба по умолчанию отключены. Перенос кода не подтверждает работу24/7 или качество отбора настоящих источников.

## Запуск и проверка

Из корня FIELD:

```sh
python3.13 -B -m collectors.source_registry.discovery.discovery --help
python3.13 -B -m unittest discover -s tests/source_registry/discovery -p test_discovery.py -v
python3.13 -B -m unittest discover -s tests -p test_inoreader_source_manager.py -v
```

CLI поддерживает один цикл `once` и повторение `serve` в foreground с паузой60min и SIGINT/SIGTERM. Supervisor/хост/расписание сам не создаёт. Обычный режим observe; apply требует отдельного разрешения, совпадающего policy hash, live-конфигурации и доказанных single/batch pilot proofs.

```sh
python3.13 -B -m collectors.source_registry.discovery.discovery once --config /absolute/private/config.json --bootstrap
python3.13 -B -m collectors.source_registry.discovery.discovery once --config /absolute/private/config.json
```

Эти команды — интерфейс, а не разрешение включить сеть/службу. Bootstrap только explicit once/observe, без сброса существующего deployment. Для повторения без ноутбука нужен отдельно согласованный runner с постоянным диском.

## Приватный config и runtime

`policy.proposed.json` содержит принятые правила и безопасный template runtime: пустые runtime_root/state_path, manager=null, три disabled providers, writer/collector_ready/pilot_proofs/delivery=false. Template сам не создаёт рабочий journal и не содержит адресов аккаунта или Mac-specific путей.

Отдельный private config задаёт `version:1`, абсолютный `policy_path`, явный абсолютный `runtime_root`, `state_path`, `deployment_id`, providers/artifacts и включения. Корневой runtime каталог создаёт оператор заранее. state_path должен быть строгим потомком `<runtime_root>/sandbox`; root/state не могут находиться в FIELD, public, dist или .git, содержать traversal или symlink ancestors. Нет fallback к cwd/папке исходников/env. Bootstrap создаёт только sandbox и child state, не произвольное дерево родителей.

Пример структуры безопасного observe config без account inputs:

```json
{
  "version": 1,
  "policy_path": "/absolute/checkout/collectors/source_registry/discovery/policy.proposed.json",
  "runtime_root": "/absolute/private/discovery-runtime",
  "state_path": "/absolute/private/discovery-runtime/sandbox/service",
  "deployment_id": "approved-deployment-id",
  "live_inputs": false,
  "writer_enabled": false,
  "collector_ready": false,
  "providers": [],
  "artifacts": [],
  "manager": null,
  "delivery": {"enabled": false}
}
```

Реальный config, SQLite/marker, receipts, OPML, query URLs, credentials и logs не включаются в Git/public/dist. Старый journal/marker не переносится автоматически. Потеря или повреждение состояния вызывает HOLD; multi-host writers не поддержаны. Локальный диск и владелец считаются доверенными; защита от злонамеренного процесса с теми же правами не заявляется.

## Цепочка

1. Читает explicit Inoreader Monitoring stream, Google Alerts/Talkwalker RSS и bounded CSV/JSON outputs существующих Source Hunter/Discovery Chain.
2. Извлекает publisher/feed из article/origin/известного wrapper, сохраняет provider/query/article provenance. Агрегатор не становится издателем; неизвестная оболочка — REVIEW. Existing68подписок служат сверкой, не новыми находками.
3. Проверяет RSS/Atom/HTML alternate/common paths по подходу Harvest. Строгий bounded HTTPS/robots/XML, public pinnedIP/TLS hostname, лимиты redirects/bytes/time. Старые Harvest packages не импортируются, dependencies не ставятся.
4. Оценивает до20уникальных заголовков: минимум10, domain+signal≥60%, даты/свежесть/язык/access. Выдаёт rule IDs, не утверждает надёжность издателя. Неизвестные/малые/спорные случаи — REVIEW.
5. Exact endpoint/alias отдельно от hostname/языка/секции. Другой профиль подписанного hostname и совпадающее окно записей удерживаются; вечная эквивалентность не предполагается.
6. Передаёт только допущенный RSS существующему Source Manager. Его алгоритм quickadd/folder/readback используется через четыре injected services; новый subscription engine не создаётся. До каждого write есть durable reservation; unknown outcome требует reconciliation, без blind retry или автоматического ремонта папки.
7. Проверяет доставку существующим collector и health. Материалы напрямую не записывает, collector на каждое подключение не диспетчеризует.

Operational SQLite хранит очередь/cursors/provenance/attempt/delivery evidence, не второй бизнес-master. Existing registry и intelligence.sources автоматически не переписываются. Для неизвестных HTML/API/sitemap остаётся routing REVIEW: универсального регистратора этих профилей в этом пакете нет.

## Лимиты и доказательства

Polling60min; до10сайтов/120HTTP/20MB/10min на цикл,1quickadd/cycle и5/day, отдельно folder5/day и discovery API200/day. Перезапуск не сбрасывает ledger.429/auth/неопределённый результат блокируют writer; другие read-only providers могут продолжаться. Код не ищет credentials/.env: OAuth env только через explicit live mapping, token rotation→REVIEW. Live квота/права не проверены переносом.

`health.py` имеет optional установленный psycopg reader с READ ONLY transaction и параметризованным origin/folder/time SELECT. Настоящий DB доступ отдельно разрешается; драйвер автоматически не устанавливается. FIRST_DELIVERY_PROVEN не равно ACTIVE. Current collector не хранит доказанный cycleID, поэтому DB reader возвращает cycleUNKNOWN; для ACTIVE нужны два доверенных самостоятельных collector-cycle receipts с новыми записями. Fake test field не создаёт реального доказательства.

Тесты используют synthetic fixtures, TemporaryDirectory, fake HTTP/DB/clock и блокируют реальные интеграции. Новый import/CLI state boundary проверяется отдельно. Unit PASS не является live API contract, precision, подпиской или cloud deployment.

После локального просмотра: отдельные exact commit/push при необходимости, guarded live executable/credential/quota scope, single Armada→реальный batch3/replay/per-origin delivery, настройка трёхрадаров и persistent runner. Нельзя выставлять pilot proof=true на основании offline tests. Publication QA/HOLD/public_release и работающие RPC/Actions/site этим пакетом не меняются.
