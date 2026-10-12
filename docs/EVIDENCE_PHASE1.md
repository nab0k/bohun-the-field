# Bohun Evidence Pipeline, Phase 1 — экспериментальный контур

Статус: **эксперимент, HOLD-only, не production-ready.** Дата: 13.10.2026. Ветка: `experiment/evidence-phase1-20261013` (от `rescue/working-model-20261012`, коммит `6cd448a`).

```
SOURCE → FULL DOCUMENT → TEXT EXTRACTION → CLAIM EXTRACTION → EXACT SPAN ALIGNMENT
       → SEMANTIC ENTAILMENT → EVIDENCE RECORD → EVALUATION / HOLD
```

## 1. Аудит существующего кода (что переиспользовано)

| Компонент | Где | Как используется в Phase 1 |
|---|---|---|
| Пилот Inoreader → оригинал → Gemini → `bohun_staging` | `scripts/inoreader-gemini-staging-pilot.py`, `.github/workflows/inoreader-gemini-staging-pilot.yml` | **Не изменён.** Его функции `assert_public_web_url` и `USER_AGENT` переиспользуются для загрузки PDF (`documents.fetch_public_pdf`), стиль REST-вызова Gemini повторён в `GeminiDirectExtractor`. |
| Сохранённые оригиналы | `bohun_staging.inoreader_documents.original_visible_text` | Вход `--source staging`, чтение в транзакции `READ ONLY` с проверкой `SHOW transaction_read_only`. Записей нет. |
| Проверка точной цитаты | `intelligence/gemini_evidence_validator.py` | Не изменён; логика «span должен дословно быть в сохранённом тексте» расширена до офсетов и хеша (`record.py`). |
| Content contract | `collectors/inoreader/content_contract.py` | Не изменён; порог «меньше 300 символов — не полный текст» взят из пилота. |
| PR #49 / #50 | — | Не тронуты. |

Последний успешный прогон пилота: run 38307753883 (12.10, `rescue/working-model-20261012`). Его логи и артефакты из этой сессии недоступны (хранилище GitHub закрыто прокси), поэтому текст статьи PAC-3 Edge в репозиторий не попадал; эксперимент читает его из staging по `--title-like "PAC-3"`.

Найдено при аудите (не исправлялось, вне задачи): на базовой ветке уже падают `tests.test_inoreader_source_manager` (ImportError: `content_contract`/`sync` не на пути) и `tests.test_signal_source_registry` (taxonomy crosslink, «Defense News RSS»). Новые изменения их не затрагивают.

## 2. Зависимости и лицензии (проверено 13.10.2026)

| Компонент | Версия / пин | Лицензия | Источник проверки | Замечание |
|---|---|---|---|---|
| LangExtract | `1.7.1` (PyPI, 08.10.2026) | Apache-2.0 | PyPI JSON | Нужна LLM; по умолчанию `enable_fuzzy_alignment=True`, `accept_match_lesser=True` — мы отключаем (см. §4). |
| MiniCheck | git `Liyan06/MiniCheck@b58b9fa69acbd1015ec970fa65dd752413a053d2` (27.08.2025) | Apache-2.0 (код); веса `lytang/MiniCheck-Flan-T5-Large` — MIT по карточке модели | GitHub, pyproject | **PyPI-пакет `minicheck` 0.4.0 — другой проект** (зависит от `z3-solver`). Ставить только из git по коммиту. |
| transformers | `4.57.6` | Apache-2.0 | PyPI | Код MiniCheck написан под 4.x; 5.x не проверялся. |
| torch | CPU wheel | BSD-style (PyPI: Apache-2.0 AND …) | PyPI | В Actions ставится с `download.pytorch.org/whl/cpu`. |
| Docling | `2.138.0` (12.10.2026) | MIT | PyPI | Тяжёлая установка (torch + модели layout); вынесено в отдельный job. |
| GLiNER | `0.2.29` | Apache-2.0 | PyPI | Не включён (по ТЗ). |
| Gemini | `gemini-3.5-flash-lite` (как в пилоте) | коммерческий API | ai.google.dev/gemini-api/docs/pricing | Платно: $0.30 / 1M input, $2.50 / 1M output. Бесплатный тариф: содержимое **используется для улучшения продуктов Google** — не подавать туда приватный контекст. Лимиты бесплатного тарифа не проверялись. |

## 3. Что сделано

- `intelligence/evidence_phase1/`
  - `record.py` — evidence record по схеме из ТЗ: `document_hash` = SHA-256 сохранённой версии текста; `char_start/char_end` проверяются срезом; только точное совпадение (без fuzzy); неоднозначный span (несколько вхождений) → HOLD; офсеты экстрактора не принимаются на веру. `verification_status="HOLD"`, `public_release=false` всегда.
  - `guards.py` — детерминированные HOLD-правила (английские cue-списки): план/отрицание/отмена/спекуляция против заявленной модальности, атрибуция без `asserted_by`, `asserted_by`, не названный в контексте. `event_date` заполняется **только** если дата видна в окне вокруг цитаты; дата публикации никогда не подставляется. Это **не классификатор**, точность не заявляется; правила могут только добавить причину HOLD. Для неанглийского текста — `GUARDS_NOT_SUPPORTED_FOR_LANGUAGE`.
  - `extractors.py` — два варианта с одинаковым выходом: `GeminiDirectExtractor` (как пилот, но до 8 claims с модальностью/атрибуцией/датой, токены из `usageMetadata`) и `LangExtractExtractor` (строгое выравнивание).
  - `entailment.py` — MiniCheck-Flan-T5-Large на окне контекста вокруг цитаты; ошибка/недоступность → HOLD с причиной, прогон не падает.
  - `documents.py` — входы: синтетические фикстуры, staging (read-only), один PDF через Docling с замером времени и памяти.
  - `metrics.py` — precision/recall/attribution/modality считаются **только при наличии gold-разметки**, иначе `NOT MEASURED`; прогноз стоимости — линейная формула с явными входами.
  - `pipeline.py` — оркестрация, жёсткий лимит 10 документов на запуск.
- `scripts/evidence-phase1-experiment.py` — CLI.
- `.github/workflows/evidence-phase1-experiment.yml` — `workflow_dispatch` (доступен после слияния в ветку по умолчанию) или push в `experiment/evidence-phase1-20261013` **только** при изменении `intelligence/fixtures/evidence_phase1/run-request.json` (тот же приём, что у пилота); параметры проверяются job `resolve` по белому списку; секреты передаются только в нужный шаг и только при выбранном режиме; входы передаются через `env` (без инъекции в shell); артефакты 7 дней, без полного текста источников.
- `tests/test_evidence_phase1.py` — 30 тестов (2 из них используют реальную LangExtract): офсеты, отрицание, планы, отмена, третьи лица, дата публикации ≠ дата события, выдуманный span, отсутствие полного текста, fail-closed при ошибке Gemini и MiniCheck, отсутствие SQL-записей в пакете, ручной запуск workflow.
- Фикстуры `intelligence/fixtures/evidence_phase1/` — **синтетические**, с вымышленными организациями, помечены как таковые; replay-кандидаты содержат намеренные ошибки для проверки guards.

## 4. Фактические результаты (что реально запускалось)

Запуски выполнены в облачном контейнере Claude (2 vCPU, 7 GB RAM). Сеть контейнера **не** имеет доступа к Hugging Face, Gemini API, PyTorch-индексу и сайтам издателей, поэтому:

| Проверка | Статус | Результат |
|---|---|---|
| Новые тесты (Python 3.13 и 3.12, langextract 1.7.1) | **ЗАПУЩЕНО** | 30/30 OK |
| Регрессия пилота: `test_gemini_evidence_validator`, `test_inoreader_content` | **ЗАПУЩЕНО** | OK |
| Офлайн replay-прогон на 3 синтетических документах | **ЗАПУЩЕНО** | 9 записей, все HOLD, 0 проблем валидации; документ-выдержка пропущен как `SKIPPED_NO_FULL_TEXT`; 0,006 с, 20,6 MB RSS |
| LangExtract alignment с подставной моделью (реальный код библиотеки) | **ЗАПУЩЕНО** | см. ниже |
| Установка MiniCheck по рецепту workflow (py3.12, torch 2.14.1, transformers 4.57.6, коммит b58b9fa) | **ЗАПУЩЕНО** | импорт OK; загрузка весов невозможна (HF недоступен) → `UNAVAILABLE:MINICHECK_LOAD_FAILED:ProxyError`, прогон завершён штатно, все записи HOLD |
| Реальный Gemini / LangExtract+Gemini / MiniCheck scoring / Docling / PAC-3 из staging | **НЕ ЗАПУСКАЛОСЬ** | нужен запуск workflow в GitHub Actions (см. §7) |

Результаты replay на gold-разметке (`precision_span_match` 0,889, `recall_essential` 1,0, `attribution_accuracy` 0,875, `modality_accuracy` 0,75) **не являются измерением качества**: кандидаты написаны вручную с намеренными ошибками. Они только подтверждают, что метрики считаются и guards ловят заложенные ошибки:

- `will deliver` с модальностью `occurred` → `PLAN_CUE_IN_SPAN_CONFLICTS_WITH_MODALITY`;
- `denied that … received a second order` как `occurred` → `NEGATION_CUE_IN_SPAN_CONFLICTS_WITH_MODALITY`;
- выдуманная цитата → `SPAN_NOT_IN_STORED_TEXT`;
- дата отмены = дата публикации → `event_date=null`, `EVENT_DATE_EQUALS_PUBLICATION_DATE_NOT_IN_EVIDENCE`;
- `may open … according to Harbour Defence Weekly` без `asserted_by` → `ATTRIBUTION_CUE_BUT_ASSERTED_BY_UNKNOWN`;
- офсеты экстрактора не совпадают с цитатой → перевыравнивание + `EXTRACTOR_OFFSETS_DO_NOT_MATCH_SPAN`.

**Найдено при запуске LangExtract 1.7.1 с настройками по умолчанию.** Перефраз `"the ministry awarded Kestrel Dynamics a radar contract"`, которого нет в тексте, получил `MATCH_LESSER` и `char_interval`, указывающий на слово `"The"`. Перефраз `"awarded … for 30 radar units"` получил `MATCH_LESSER` на обрезанном фрагменте без слов `counter-drone radar units`. С `enable_fuzzy_alignment=False, accept_match_lesser=False` оба получают `char_interval=None`. Значит, «grounding» LangExtract в режиме по умолчанию нельзя считать доказательной привязкой; в контуре Phase 1 он отключён, а каждый span дополнительно перепроверяется срезом.

Известное ограничение guards: правила видят только окно вокруг цитаты. Если отмена описана в соседнем предложении («signed … . On 2 October … cancelled»), запись о подписании не получает `CANCELLATION_CUE_IN_CONTEXT` (тест фиксирует это поведение). Связь событий между предложениями — задача следующей фазы, а не правил.

## 5. Сравнение: текущий пилот vs Phase 1

| | Текущий пилот | Phase 1 |
|---|---|---|
| Claims на документ | ровно 1 | до 8 |
| Привязка | span как подстрока первых 12 000 символов | span + `char_start/char_end` + SHA-256 версии текста; неоднозначность → HOLD |
| Модальность / атрибуция / дата события | нет | поля + детерминированные HOLD-guards; дата события только из текста |
| Semantic entailment | нет | MiniCheck (EN), fail-closed |
| Полнота | не измеряется | recall на gold-разметке; без неё `NOT MEASURED` |
| Запись | `bohun_staging` (2 таблицы) | **нет записей**; только артефакты Actions |
| Стоимость | 1 вызов Gemini, токены пишутся | токены пишутся (direct); у LangExtract не доступны (`NOT_MEASURED`) |

Качество Gemini-direct против LangExtract+Gemini на реальных текстах: **NOT MEASURED** до запуска workflow и до разметки gold-выборки.

## 6. Оценка расходов (предположения, не измерения)

Формула: `metrics.project_costs`. Цены: `gemini-3.5-flash-lite` paid tier $0.30 / 1M input, $2.50 / 1M output (страница цен Google, 13.10.2026). Предположения (заменить измеренными после первого запуска):

- Gemini-direct: 2 000 input + 700 output токенов на документ (≈6 000 символов статьи + инструкция; 8 claims). Thinking-токены не учтены.
- LangExtract: 4 000 input + 800 output (≈4 чанка по 1 500 символов, few-shot пример повторяется в каждом чанке).
- MiniCheck на CPU: **8 с на документ** (≈1 с на claim × 8) — неподтверждённое предположение (уровень E).
- GitHub Actions: стандартные раннеры бесплатны и без лимита минут для публичных репозиториев (4 vCPU, 16 GB); для приватного — 2 vCPU, 8 GB и платные минуты.

| Документов/сутки | Gemini-direct, $/сутки (30 дней) | LangExtract, $/сутки (30 дней) | MiniCheck, раннер-часов/сутки | Параллельных раннеров |
|---|---|---|---|---|
| 1 000 | 2,35 (70,5) | 3,20 (96) | 2,2 | 0,1 |
| 10 000 | 23,5 (705) | 32 (960) | 22,2 | ~1 |
| 100 000 | 235 (7 050) | 320 (9 600) | 222 | ~9,3 |

Выводы с оговорками: до ~1 000 документов/сутки всё укладывается в бесплатные раннеры и единицы долларов на LLM; при 100 000 документов/сутки GitHub Actions уже неподходящая платформа (джобы до 6 ч, лимит параллельности), и потребуется отдельное решение, которое ТЗ запрещает принимать без разрешения. Бесплатный тариф Gemini не рассматривается как основа: его лимиты не проверены, а данные используются Google.

## 7. Как запустить в облаке

1. Пока workflow не в ветке по умолчанию, GitHub не даёт `workflow_dispatch` (API отвечает 404). Поэтому запуск — коммит в `intelligence/fixtures/evidence_phase1/run-request.json` на ветке эксперимента; после слияния — `Actions → Evidence Phase 1 Experiment → Run workflow`.
2. Рекомендуемая последовательность (каждый шаг ≤ 3 документов):
   - `source=fixtures, extractor=replay, entailment=minicheck` — проверка загрузки MiniCheck, CPU/RAM, без затрат на LLM.
   - `source=fixtures, extractor=gemini-direct, entailment=minicheck` — первые реальные метрики на синтетической gold-разметке.
   - то же с `extractor=langextract`.
   - `source=staging, title_like=PAC-3, extractor=gemini-direct, entailment=minicheck` — PAC-3 Edge, без gold → качество `NOT MEASURED`.
   - `docling_pdf_url=<публичный PDF>` — замер Docling.
3. Скачать артефакт `evidence-phase1-*`: `*-records.jsonl`, `*-summary.json`, `*-time.txt`.

Локально (без секретов): `python -m unittest tests.test_evidence_phase1 -v` и `python scripts/evidence-phase1-experiment.py --source fixtures --extractor replay`.

## 8. Ограничения и риски

- **Не production-ready.** Нет gold-разметки реальных документов, поэтому реальные precision/recall не измерены.
- MiniCheck, AlignScore и guards — только английский. DE/PL/FR/NL/UK не поддерживаются, пока не будут отдельно испытаны.
- `original_visible_text` пилота включает навигацию и шаблонный текст страницы; извлечение основного текста статьи в Phase 1 не меняется.
- Публичный репозиторий: артефакты Actions могут скачать любые пользователи GitHub. Поэтому полный текст не выгружается, только короткие цитаты и хеши; срок хранения 7 дней. Права на цитирование отдельно не проверены.
- Entailment подтверждает только то, что claim следует из цитаты, а не то, что claim истинен.
- Прогноз стоимости построен на предположениях до первого реального запуска.
