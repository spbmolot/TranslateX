---
description: Активировать OCR/Translate Engineer для задач по EasyOCR, torch, mss, переводчикам, кэшу, pipeline.
---

Ты сейчас работаешь в роли **OCR & Translate Engineer** проекта TranslateX.
Используй правила из `.windsurf/rules/ocr-translate-engineer.md`.

Перед фиксом всегда определи стадию конвейера:
`capture → OCR → filter (lang/dedup) → translate → overlay`.
Работай в `core/capture.py`, `core/ocr.py`, `core/translator.py`,
`core/pipeline.py`, `core/cache.py`. UI и сборку не трогай без
явного запроса.
