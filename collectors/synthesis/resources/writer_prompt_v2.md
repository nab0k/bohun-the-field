# Писатель синтеза — промпт и JSON-схема v2

28.10.2026, Claude (A-18 v2, по offline-отчёту A-07). Изменения против v1: «Why it matters» может быть пустым — тогда код держит синтез (`HOLD why_unsupported`), а не публикует (в базе `why_it_matters NOT NULL`); входы — `topics_v2.json`, `dictionaries_v2.json`.

Как в v1: для шага 5 маршрута (EXP-024): **один вызов Gemini на синтез**. Модель получает только пакет фактов; всё, что не в пакете, писать нельзя. Код после вызова проверяет каждое предложение (шаг 6). Язык вывода — английский.

## 1. Системная инструкция (передаётся как есть)

```text
You write one news synthesis for BOHUN, a defence and dual-use industry intelligence feed.
Readers are business-development, procurement and supply-chain professionals.

Use ONLY the facts in the packet. Every sentence you write must list the fact IDs it relies on.
Never add a fact, number, name, date, cause, consequence or forecast that is not in the cited facts.

Rules:
1. Keep each fact's modality exactly: "planned" -> plans/intends/aims; "announced" -> announced;
   "signed" -> signed/awarded/won; "delivered" -> delivered; "completed" -> completed; "negated" -> dropped/cancelled/did not.
   Never turn a plan into a deal, or an approval into a contract.
2. Keep each money fact's commitment status: framework, approval (export approval is not a contract), contract,
   ceiling ("up to"), funded, delivered. Write "up to" for ceilings.
3. If the packet marks a value as not disclosed, write "not disclosed". Do not estimate.
4. No superlatives or hype (largest, first-ever, breakthrough, historic, massive) unless the cited fact's quote contains it.
5. No forecasts. "Why it matters" states a concrete implication for suppliers, buyers or partners that follows
   directly from the cited facts; it does not predict.
6. Neutral tone. Do not copy more than 12 consecutive words from any quote.
7. Name companies as given in the facts. Do not describe a company's role beyond the cited facts.
8. Attribute when the packet says the fact has no primary source: "according to <outlet>".
9. Write a block only if the packet provides facts for it. Leave the block as an empty list otherwise.
   This includes "why_it_matters": if no concrete implication follows directly from the cited facts,
   return an empty list. Do not invent one to fill the block.
10. Headline: "Who — what — where", max 90 characters, no clickbait.

Return JSON that matches the provided schema. No text outside JSON.
```

## 2. Пакет на входе (собирает код)

```json
{
  "event": {
    "type": "contract",
    "first_published_at": "2026-09-14",
    "is_rewrite": true,
    "topic": "US missile and munitions production ramp-up",
    "parent_event_title": null
  },
  "facts": [
    {"id": "F1", "block": "what_happened", "text": "Raytheon completed a 90-day SM-6 production sprint at Huntsville, Alabama.",
     "modality": "completed", "commitment": null, "amount": null, "source": "RTX", "is_primary": true,
     "quote": "…"},
    {"id": "F2", "block": "what_happened", "text": "The sprint target and quantities were not disclosed.",
     "modality": "unknown", "not_disclosed": true, "source": "RTX", "is_primary": true, "quote": "…"},
    {"id": "C1", "block": "context", "text": "On 1 Oct 2026 the Navy awarded Raytheon a multiyear SM-6 contract of up to $24.4 billion.",
     "modality": "signed", "commitment": "ceiling", "amount": 24400000000, "currency": "USD", "date": "2026-10-01", "source": "RTX", "is_primary": true},
    {"id": "D1", "block": "policy_direction", "text": "…"},
    {"id": "B1", "block": "bigger_picture", "text": "…"},
    {"id": "S1", "block": "supply_chain", "text": "…"}
  ],
  "sources": [
    {"outlet": "RTX", "url": "https://…", "date": "2026-09-14", "role": "primary"},
    {"outlet": "Defence Industry Europe", "url": "https://…", "date": "2026-10-28", "role": "rewrite"}
  ]
}
```

Тексты фактов слоёв `context`, `policy_direction`, `bigger_picture`, `supply_chain` код формирует из базы шаблонами (хронология, доктрина, вертикаль, ограничения), а не из прошлых синтезов. Префиксы ID: `F` — факты события, `C` — хронология, `D` — доктрина, `B` — тема, `S` — вертикаль и ограничения.

## 3. JSON-схема ответа (для structured output)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["headline", "what_happened", "context", "policy_direction", "bigger_picture", "supply_chain", "why_it_matters"],
  "properties": {
    "headline": {
      "type": "object", "additionalProperties": false, "required": ["text", "fact_ids"],
      "properties": {
        "text": {"type": "string", "maxLength": 90},
        "fact_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}
      }
    },
    "what_happened":    {"$ref": "#/$defs/sentences", "minItems": 1, "maxItems": 3},
    "context":          {"$ref": "#/$defs/sentences", "maxItems": 3},
    "policy_direction": {"$ref": "#/$defs/sentences", "maxItems": 2},
    "bigger_picture":   {"$ref": "#/$defs/sentences", "maxItems": 1},
    "supply_chain":     {"$ref": "#/$defs/sentences", "maxItems": 2},
    "why_it_matters":   {"$ref": "#/$defs/sentences", "minItems": 0, "maxItems": 1}
  },
  "$defs": {
    "sentences": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false, "required": ["text", "fact_ids"],
        "properties": {
          "text": {"type": "string", "maxLength": 400},
          "fact_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}
        }
      }
    }
  }
}
```

Если API не принимает `$ref`, код разворачивает определение в каждый блок. Шапка с типом, темой и новизной, а также блок «Sources» собираются **кодом**, не моделью.

## 4. Параметры вызова

- Модель — платный Flash-класс по согласованию (EXP-024 §6); температура 0,2; потолок выходных токенов 1200; один вызов, без повторов при отказе схемы (отказ → `HOLD text_mismatch`).
- Перед вызовом — оценка токенов и резерв бюджета; после — фактическая стоимость из usage; неизвестная стоимость — стоп прогона.

## 5. Что проверяет код после вызова (напоминание для A-07)

Пустой `why_it_matters` → `HOLD why_unsupported` (синтез не пишется в `synthesis_version`, событие ждёт новых фактов). Пустой `what_happened` или заголовок → отказ схемы → `HOLD text_mismatch`.

Каждое предложение: есть `fact_ids` и все они в пакете и в разрешённом блоке; числа, валюты, даты и имена совпадают с фактами; модальные слова соответствуют модальности фактов (словарь `dictionaries_v2.json`); отрицания сохранены; нет слов из `banned_words_unless_quoted` и `forecast_markers`; нет 13+ слов подряд из цитат.
