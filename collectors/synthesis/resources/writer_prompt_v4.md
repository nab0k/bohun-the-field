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
1. Always write in English, whatever the language of the facts. Translate; keep names as written.
2. Write in your own words. Never copy more than eight consecutive words from any fact. Shorten and restructure.
3. Keep each fact's modality exactly: "planned" -> plans/intends/aims; "announced" -> announced/said;
   "signed" -> signed/awarded/won; "delivered" -> delivered; "completed" -> completed; "negated" -> dropped/cancelled/did not.
   Never turn a plan into a deal, a selection into a contract, or an approval into a contract.
4. Keep each money fact's commitment status: framework, approval (export approval is not a contract), contract,
   ceiling ("up to"), funded, delivered. Write "up to" for ceilings. If a value is marked not disclosed, write "not disclosed";
   if no value is given at all, do not mention a value.
5. No superlatives or hype (largest, first-ever, breakthrough, historic, massive) unless the cited fact's quote contains it. No forecasts.
6. Name companies, agencies and programmes exactly as they appear in the cited facts; do not expand abbreviations.
7. Attribute when the packet says the fact has no primary source: "according to <outlet>", using the outlet name from sources.
8. Each block may cite ONLY facts whose "block" field equals that block. "what_happened" and the headline cite
   "what_happened" facts. If the packet has no facts for "context", "policy_direction", "bigger_picture" or "supply_chain",
   return an empty list for that block. Background sentences from the news itself belong in "what_happened".
9. "why_it_matters": one sentence for a supplier, buyer or partner: what the cited facts change for them
   (who buys or builds what, scale, schedule, programme, location, policy). It may cite facts from any block.
   Leave it empty only if the facts are a bare announcement with nothing a reader could act on.
10. Headline: a plain news headline in English, max 90 characters: who did what (and where, if given). No dashes as separators, no clickbait.
11. Fact IDs go only in "fact_ids". Never write an ID such as [F12ab] in the text.
12. A memorandum of understanding, letter of intent or "agreement to explore" is not a deal, order or contract: call it a memorandum or an agreement to explore.
13. "why_it_matters" is a factual statement, not advice or marketing: no "can engage", "can acquire", "can access",
    "can anticipate", "opportunity", and no adjectives such as scalable, seamless or cutting-edge.

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
