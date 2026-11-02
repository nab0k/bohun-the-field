# EXP-016 — disposable PostgreSQL proof

Подготовленный пакет. Ни SQL, ни облачный workflow ещё не выполнялись.
Все профили/источники в harness синтетические. Программа компаний не запущена
на реальных карточках и не стала постоянной облачной службой.

23 файла backend-v0_1 сохранены побайтно; новые копии postgres.py и SQL candidate
разрешены для минимальных compatibility fixes. Исходный frozen пакет не меняется.
Оригинальная схема001 также копируется без изменений. Изменения видны в diff.

Baseline profile SQL восстановлен по отдельному разрешённому schema-only чтению
02.11: 4 таблицы/33 столбца/28 constraints, включая NOT NULL/default/IDENTITY.
Сервер вернул17.11. Это точная адресная часть схемы, не dump всей Supabase:
company/observation берутся из оригинального001, роли/расширения и вся среда
Supabase не копируются. Полное production соответствие не заявляется.

`bootstrap.sql` также содержит закрытые fixture.profile/source и тестовые
функции. Настоящие reader/source hooks первоначально отсутствуют; тесты сначала
проверяют отказ, затем администратор тестовой БД устанавливает synthetic wrappers.
Их elevated owner нужен только для доступа к synthetic fixture. Это не шаблон
для production reader; нет автоматического переноса таких прав в рабочую базу.

`rpc_transport.py` использует только fixed RPC и bind parameters, отдельное
соединение/транзакцию. Успех выдаётся после COMMIT. SQLSTATE40001/40P01 означает
известный rollback; пропавший ответ COMMIT — UnknownCommit. Ни транзакция,
ни внешняя модель не повторяются автоматически. Endpoint строго localhost/
exp016_proof/worker; password исключён из repr. Нет shared PgStore/env fallback.

`environment.py` не запускается в prepare/live/cleanup без cloud/manual/SHA/
public-repository guards. Проверяет hashes, устанавливает только два закреплённых
wheels в облаке, запускает pinned PostgreSQL image с loopback binding и tmpfs,
создаёт случайные временные passwords, удаляет свой контейнер по имени и label.
Внутренние stage hashes исключают только fixtures.json из-за самоссылки;
неизменность полного пакета связывается с отдельным manifest и approved Git SHA.
Нет artifact/cache uploads, расписаний, рабочих credentials или моделей.

Подготовка здесь допускает только AST/compile и stdlib offline suite, без
SQL/psycopg/Docker и установок на Mac. Отдельно загружается test_protocol, но
не test_postgres_live. Случайный запуск live-тестов вне cloud вызывает ошибку,
не skip/pass. Live содержит23реальных сценария: DDL/defaultcontrol/RLS/role denial,
hashes/Unicode/time, claim race/reclaim, manual CAS, prepared restart/privatewriter,
unknownACK/atomic rollback, source/rejected/manual/unknownowner guards, negative
research, approved coverage, actual serialization/stop race, UA и полный3751обход.

Миграция пересоздаёт **только disposable** companies/fixture schema для каждого
теста. Стенд нельзя направить на рабочий host/database. Raw admin harness не
используется runtime adapter; его пароль и роль никогда не поступают worker.
23сценария пока NOT_RUN; синтаксис и транзакционные гарантии ждут облачной пробы.

Версии/pins сверены по первичным metadata без скачивания image/wheels.
[PostgreSQL17: изоляция транзакций](https://www.postgresql.org/docs/17/transaction-iso.html)
описывает serialization aborts; [блокировки](https://www.postgresql.org/docs/17/explicit-locking.html)
— конфликты разных сессий. [CREATE FUNCTION](https://www.postgresql.org/docs/17/sql-createfunction.html)
и [ALTER FUNCTION](https://www.postgresql.org/docs/17/sql-alterfunction.html)
использованы для разбора частной границы прав. Schema-qualified substring записан
в обычной comma-форме согласно [строковым функциям](https://www.postgresql.org/docs/17/functions-string.html).
[GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
подтверждает бесплатный compute стандартных public runners; текущий repository/
SHA/runner проверяются заново перед отдельным запуском. Большие runners и storage
этим выводом не покрываются. PyPI/OCI pins и метаданные — environment.lock.json.

Далее: просмотр exact diff/SQL/pins, отдельно перенос/Git/merge и один run<=20min.
После него нужны проверка результатов и отдельная рабочая интеграция с A26,
native/source adapters, free-only admission и реальным private30+10. Не публиковать
HOLD/REJECTED и не включать Notion/site/cron из этого пакета.

В SQL-копии также восстановлена проверка актуального времени после ожидания locks
и перед завершением: истёкшая за время ожидания lease не разрешает commit.
Отдельный реальный сценарий23 проверит это; он пока NOT_RUN.
