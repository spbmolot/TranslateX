# Performance Backlog — TranslateX

> **Цель:** псевдо-real-time перевод (~300–500 мс end-to-end) на CPU
> + снижение нагрузки на CPU/RAM.
> **GPU-задачи отложены** до появления CUDA в окружении.

## Статус (обновлено 2026-07-06)

| Задача | Статус | Примечание |
|---|---|---|
| PERF-001 бенчмарк-харнесс | ✅ сделано | `pytest -m perf`, `scripts/run_bench.py --baseline/--compare`; baseline в `tests/benchmark/baseline.json` |
| PERF-002 downsample перед OCR | ✅ сделано | `core/ocr.py: preprocess_for_ocr` — auto (>720p → 720p, клэмп ×0.5) / off / фикс. коэффициент; bbox масштабируются обратно; настройка в UI |
| PERF-004 async-batch translation | ✅ сделано | `ThreadPoolExecutor(4)` в `_translate_google`; порядок сохраняется; фейл одного блока не роняет остальные |
| PERF-011 keep-alive Session | ✅ сделано | Подмена модульного `requests` в `deep_translator.google` на `Session` с connection pooling |
| PERF-013 захват без скрытия overlay | ✅ сделано (иначе) | Вместо DXGI — `WDA_EXCLUDEFROMCAPTURE` (Win10 2004+): overlay исключён из BitBlt/mss-захвата, мерцание убрано. Эмпирическая проверка: `scripts/check_capture_exclusion.py`. Fallback hide/show сохранён |
| PERF-008 CPU-only torch | ✅ уже так | В окружении `torch 2.10.0+cpu`, CUDA-DLL в dist нет. Dist 1.09 ГБ — это вес самого torch CPU + PyQt5 + cv2 + scipy. Дальнейшее похудение — только через PERF-006 (ONNX, убирает torch целиком) |
| PERF-003 lazy-load OCR | ✅ сделано | Worker создаётся при первом start(); клик «Старт» не блокируется — pipeline ждёт Reader и стартует сам. Примечание: torch импортируется в main.py до PyQt5 (DLL-порядок), экономится Reader+модели, не сам torch |
| PERF-005 incremental repaint | ✅ сделано | `update_blocks` пропускает repaint при неизменной сигнатуре (текст+bbox+цвет+регион); на статичном экране 0 перерисовок |
| PERF-012 PreciseTimer | ✅ сделано | `pipeline.py`: `setTimerType(Qt.PreciseTimer)` |
| PERF-006 ONNX + INT8 | ⬜ не начато | Следующий крупный шаг, см. план в карточке ниже |
| PERF-007 detector-skip | ⬜ не начато | |
| PERF-009 adaptive threshold | ❌ отклонено | Конфликтует с фиксом frame-diff: авто-повышение порога по EMA заново вводит риск «не заметили локальное изменение» (устаревший перевод), ради экономии OCR на видео-сценах, где и так работает resync-кэп 30 скипов. Не делаем, пока нет реальных жалоб на CPU при видео |
| PERF-010 pipelining | ⬜ не начато | |

**Бенчмарк-подтверждение PERF-002** (VM-baseline 2026-07-06):
EasyOCR readtext 1440p: downscale=auto **32.3 c** vs off **69.2 c** → **×2.14**
(машина медленная, важна пропорция; DoD ≥40% выполнен).

Кроме perf: сделаны группировка строк в абзацы (`core/paragraphs.py`),
inpaint-подложка (`core/inpaint.py` + `ui/overlay.py`), фиксы кэша
переводов (не кэшировать фейлы; ключ с provider/source), тайловый
frame-diff (`_frame_unchanged`), честный режим `auto` для исходного языка.

## Легенда

- **Owner**: основной агент-исполнитель (см. `.windsurf/rules/`).
- **Tier**: 1 — основные win'ы малой ценой; 2 — средние; 3 — микро; 4 — стратегические.
- **DoD** (Definition of Done): что должно быть в наличии для закрытия.
- **Files**: основные затрагиваемые файлы.

---

## Tier 0 — БЛОКЕР (делаем первой задачей)

### `PERF-001` Бенчмарк-харнесс с baseline
- **Owner**: `qa-tester` + поддержка `ocr-translate-engineer`
- **Tier**: 0 (блокирует все остальные PERF-задачи)
- **Cost**: 1 день
- **Files**:
  - `tests/benchmark/__init__.py` (новый)
  - `tests/benchmark/test_perf.py` (новый)
  - `tests/benchmark/fixtures/sample_*.png` (фикстуры — английский текст разных размеров)
  - `tests/benchmark/baseline.json` (baseline-замеры до оптимизаций)
  - `pyproject.toml` / `pytest.ini`: marker `perf`, исключённый из обычного `pytest tests`
  - `scripts/run_bench.py` (CLI: запуск + diff с baseline + красивый отчёт)
- **Что замеряем**:
  - `mss.grab` 640×220 × 100 → median, p95.
  - `EasyOCR.readtext` cold/warm на фиксированном PNG → ×10 → median.
  - `TranslationJobThread.run` cache-miss / cache-hit → ×10.
  - End-to-end: имитировать `set_region` + monkey-patch `capture_to_numpy` → измерить timer→`translation_ready` × 10.
  - RAM peak `psutil.Process().memory_info().rss` за 60 сек работы.
- **DoD**:
  - `pytest -m perf -q` гонит харнесс.
  - `python scripts/run_bench.py --baseline` сохраняет `baseline.json`.
  - `python scripts/run_bench.py --compare` показывает diff в %.
  - В обычном `pytest tests` бенчмарки **не запускаются**.
  - В `docs/PERFORMANCE_BACKLOG.md` появляется секция «Baseline (дата)» с табличкой median/p95.

---

## Tier 1 — Основные win'ы (быстрая итерация)

### `PERF-002` Downsample + grayscale перед EasyOCR
- **Owner**: `ocr-translate-engineer`
- **Tier**: 1
- **Cost**: ½ дня
- **Эффект (ожид.)**: ×1.5–3 OCR на CPU, без потери recall на шрифте ≥14 px.
- **Files**:
  - `core/ocr.py` (`OcrJobThread.run` ~ строки 116–130)
  - `core/pipeline.py` (передавать blocks с правильным масштабом bbox обратно в overlay)
  - `tests/test_ocr_preprocess.py` (новый): проверка корректности bbox после ресайза
- **Реализация**:
  - cv2 → grayscale (3 → 1 канал).
  - Если `H > 480` — масштабировать до 480 px высоты, `cv2.INTER_AREA`.
  - Запомнить `scale` → bbox умножать обратно на `1/scale`.
  - Конфиг `[ocr].downscale = auto | off | <число>` (default `auto`).
  - Если шрифт мелкий (детект через первый прогон без downscale) — отключить.
- **DoD**:
  - bbox точно ложится поверх оригинального ROI (визуально проверить overlay).
  - Recall ≥95 % от baseline на тестовом PNG со шрифтом 14/18/24 px.
  - Бенчмарк `PERF-001` показывает ускорение ≥40 %.
  - 0 регрессий в `pytest tests`.

### `PERF-003` Lazy-load EasyOCR Reader
- **Owner**: `ocr-translate-engineer` + `packaging-engineer` (review)
- **Tier**: 1
- **Cost**: 1–2 часа
- **Эффект (ожид.)**: idle RAM 800 → ~200 МБ; cold start 4 → ~1 сек.
- **Files**:
  - `core/ocr.py` (`OcrWorker.__init__` / `run`)
  - `core/pipeline.py` (`_on_timer_tick` — graceful wait если reader не готов)
  - `main.py` (НЕ грузить OCR при создании `App`, только по `set_region` или `start`)
  - `ui/region_frame.py` (показывать «Загрузка OCR…» статус при первом старте)
- **Реализация**:
  - `OcrWorker.start()` сейчас вызывается в `AppController.__init__` (`pipeline.py:85`).
    Перенести в `set_region()` или первый `start()`.
  - Pipeline уже умеет ждать reader (`_warned_not_ready` flag) — UI должен показать прогресс.
- **DoD**:
  - `Get-Process TranslateX | Select WorkingSet64` после старта в трее ≤250 МБ.
  - Cold start (от запуска до появления иконки в трее) ≤1.5 сек.
  - При первом hotkey Ctrl+Shift+T — UI показывает «Загрузка OCR (≤5 с)…» и работает после готовности.
  - Бенчмарк `PERF-001` фиксирует RAM_idle.

### `PERF-004` Async-batch translation
- **Owner**: `ocr-translate-engineer`
- **Tier**: 1
- **Cost**: ½ дня
- **Эффект (ожид.)**: 5 блоков cache-miss: 1250 → ~350 мс.
- **Files**:
  - `core/translator.py` (`TranslationJobThread._translate_google` строки 137–149)
  - `tests/test_translator.py` (новый или расширить существующий): мок-`GoogleTranslator`
- **Реализация**:
  - `concurrent.futures.ThreadPoolExecutor(max_workers=4)` для cache-miss блоков.
  - DeepL уже batch — не трогать.
  - Сохранить порядок результатов через `executor.map` или сборку по индексу.
  - Учесть глобальный rate-limit deep-translator: при 429 — fallback на sequential.
- **DoD**:
  - `pytest -k translator` зелёный.
  - Бенчмарк `PERF-001` (5 блоков, cache miss) показывает ускорение ≥60 %.
  - При искусственно сбойном переводе одного блока — остальные доходят.

### `PERF-005` Incremental overlay repaint
- **Owner**: `qt-ui-engineer`
- **Tier**: 1
- **Cost**: ½ дня
- **Эффект (ожид.)**: paint 2–5 → 0.5 мс; на 4K −10× UI thread.
- **Files**:
  - `ui/overlay.py` (`set_blocks`, `paintEvent`)
  - `tests/test_overlay.py` (новый): проверка `update(QRect)` региона
- **Реализация**:
  - Хранить предыдущий список bbox'ов.
  - При новом `set_blocks` считать `QRegion(union_old) | QRegion(union_new)` → `update(region.boundingRect())`.
  - Если набор bbox'ов идентичен по позициям и текст не изменился — `update()` не вызывать вообще (там же делает pipeline через `_last_translated_blocks`, но overlay тоже должен уметь skip).
- **DoD**:
  - При статичном тексте `update()` не зовётся (логирование DEBUG в `paintEvent`).
  - Визуально нет «хвостов» при изменении текста.
  - Бенчмарк `PERF-001` фиксирует UI-thread CPU.

---

## Tier 2 — Сложнее, окупается на CPU-only

### `PERF-006` ONNX + INT8 quantized recognizer
- **Owner**: `ocr-translate-engineer` + `packaging-engineer` (build.spec)
- **Tier**: 2
- **Cost**: 2–3 дня
- **План выполнения (уточнён 2026-07-06):**
  1. `pip install onnxruntime` (только dev; в requirements — после проверки recall).
  2. `scripts/export_onnx.py`: загрузить CRAFT (`craft_mlt_25k.pth`) и
     `cyrillic_g2.pth` через easyocr-классы → `torch.onnx.export`
     (dynamic axes по H/W и длине последовательности) →
     `onnxruntime.quantization.quantize_dynamic` (INT8).
  3. `core/ocr_backends/onnx_backend.py`: препроцессинг как в EasyOCR
     (resize_aspect_ratio + normalizeMeanVariance), постпроцессинг
     `getDetBoxes` можно импортировать из `easyocr.craft_utils` (лёгкий, без torch).
  4. Валидация: `pytest -m perf` + фикстуры `make_text_image` — recall ≥90%
     от EasyOCR baseline (`tests/benchmark/baseline.json` уже содержит
     `ocr_readtext_720p_*` для сравнения).
  5. Только после этого: выкинуть torch из build.spec → dist ~1.09 ГБ → ~350-450 МБ,
     исчезает весь DLL-хак `_prepare_torch_dll_paths` в main.py.
- **Эффект (ожид.)**: ×2–4 на CPU; RAM −400 МБ (нет full torch).
- **Files**:
  - `core/ocr.py`: новый backend `OnnxOcrEngine` (alternative to EasyOCR Reader)
  - `core/ocr_backends/__init__.py`, `core/ocr_backends/easyocr_backend.py`, `core/ocr_backends/onnx_backend.py` (новые)
  - `requirements.txt`: `onnxruntime`
  - `build.spec`: hidden imports `onnxruntime`
  - `scripts/export_onnx.py` (новый): экспорт `craft_mlt_25k.pth` + `english_g2.pth` в ONNX + квантизация INT8
  - `assets/models/*.onnx` (артефакты — НЕ в git, скачиваются на первом старте если backend=onnx)
- **Реализация**:
  - Конфиг `[ocr].backend = easyocr | onnx` (default — `easyocr`).
  - Factory в `pipeline.py` создаёт нужный backend по конфигу.
  - INT8-quantization через `onnxruntime.quantization.quantize_dynamic`.
- **DoD**:
  - На том же PNG `onnx`-backend даёт ≥90 % recall от EasyOCR baseline.
  - Speedup ≥×2 (бенчмарк).
  - RAM peak ≤ baseline − 300 МБ.
  - Тогглить через Settings UI без перезапуска (требует full restart воркера — допустимо).

### `PERF-007` Detector-skip на стабильных кадрах
- **Owner**: `ocr-translate-engineer`
- **Tier**: 2
- **Cost**: 1 день
- **Эффект (ожид.)**: ×2–3 на статичных интерфейсах.
- **Files**:
  - `core/ocr.py`: использовать `Reader.detect()` + `Reader.recognize()` отдельно вместо `readtext()`
  - `core/pipeline.py`: новый кэш `_last_bboxes` + проверка IoU
- **Реализация**:
  - После первого OCR сохраняем bbox'ы.
  - Frame-diff показал «слабые изменения, но не нулевые» → запускаем только recognizer на старых bbox'ах + быстрый detector раз в 3–5 итераций.
  - Если detector нашёл новые bbox'ы (IoU < 0.7 с предыдущими) — полный re-run.
- **DoD**:
  - На статичной странице (например, документ Word) — recognizer-only итерации ≤200 мс.
  - Скролл текста корректно вызывает full detector pass.
  - Бенчмарк отдельный: «статичный текст 30 итераций».

### `PERF-008` torch CPU-only build
- **Owner**: `packaging-engineer`
- **Tier**: 2
- **Cost**: 1 день
- **Эффект (ожид.)**: dist 1.09 ГБ → ~450 МБ; installer 335 → ~150 МБ; idle RAM −300 МБ.
- **Files**:
  - `requirements.txt` (CPU-only torch index)
  - `requirements-gpu.txt` (новый — для будущей GPU-сборки)
  - `build.bat` / `build.spec` (исключить torch_cuda*.dll, cudnn*.dll)
  - `docs/BUILD.md` (как пересобрать GPU-вариант)
- **Реализация**:
  ```
  pip install --upgrade --force-reinstall \
      torch torchvision \
      --index-url https://download.pytorch.org/whl/cpu
  ```
  - В `build.spec` использовать `excludes` чтобы PyInstaller не тащил CUDA DLL'ки если они случайно остались.
- **DoD**:
  - `dist\TranslateX\` ≤500 МБ.
  - Installer ≤200 МБ.
  - Smoke-тест на чистой VM: установка → запуск → OCR ready → 0 ERROR.
  - Документация `docs/BUILD.md` обновлена.
- **Зависит от**: `PERF-001` (нужен бенчмарк, чтобы не уронить производительность).

---

## Tier 3 — Микро-оптимизации

### `PERF-009` Adaptive frame-diff threshold
- **Owner**: `ocr-translate-engineer`
- **Tier**: 3
- **Cost**: 2 часа
- **Эффект**: ×2 fewer OCR на видео-сценах; нейтрально на статике.
- **Files**: `core/pipeline.py` (`_frame_unchanged` ~246–258, добавить EMA от motion).
- **Реализация**: вести EMA от `np.abs(sig - prev).mean()`, threshold = `max(1.0, 2 * EMA)`.
- **DoD**: при фиксированном экране threshold скатывается к ~1.0; при динамическом видео — к 4–6.

### `PERF-010` TR-1: Pipelining capture/translate
- **Owner**: `ocr-translate-engineer`
- **Tier**: 3
- **Cost**: 2 часа
- **Эффект**: throughput +30 %.
- **Files**: `core/pipeline.py` (`_on_ocr_complete` — `_reschedule_tick()` СРАЗУ перед запуском translation).
- **DoD**: capture следующего кадра идёт параллельно с translation предыдущего; нет race в `_last_translated_blocks`.

### `PERF-011` TR-3: requests.Session keep-alive
- **Owner**: `ocr-translate-engineer`
- **Tier**: 3
- **Cost**: 1 час
- **Эффект**: cache miss 200–600 → 80–200 мс (TLS reuse).
- **Files**: `core/translator.py` — кэшировать `GoogleTranslator` instance с `requests.Session`.
- **DoD**: бенчмарк показывает ускорение cache-miss ≥30 %.

### `PERF-012` Qt.PreciseTimer для capture loop
- **Owner**: `qt-ui-engineer`
- **Tier**: 3
- **Cost**: 5 минут
- **Эффект**: стабильность tick ±2 мс вместо ±20 мс.
- **Files**: `core/pipeline.py:91` — `self._timer.setTimerType(Qt.PreciseTimer)`.
- **DoD**: бенчмарк tick-jitter показывает уменьшение std с ~10 мс до ~2 мс.

---

## Tier 4 — Стратегические (требуют отдельного решения)

### `PERF-013` DXGI Desktop Duplication API для capture без overlay-hide
- **Owner**: `qt-ui-engineer` + `ocr-translate-engineer`
- **Tier**: 4
- **Cost**: 1–2 дня
- **Эффект**: убирает мерцание overlay при каждом capture; CPU −5–10 мс/тик.
- **Files**:
  - `core/capture.py` (новый backend `dxgi`)
  - `core/pipeline.py` (убрать `capture_about_to_start` / `capture_finished` если backend=dxgi)
- **Реализация**: использовать `Windows.Graphics.Capture` (Win10+) через `pywin32` + COM, либо готовую обёртку (например, `desktopmagic` / `dxcam`).
- **DoD**: capture без скрытия overlay; visual smoothness без мерцания; recall OCR не упал.

### `PERF-014` Two-installer стратегия (CPU + GPU)
- **Owner**: `packaging-engineer`
- **Tier**: 4
- **Cost**: 1 день
- **Зависит от**: `PERF-008` (CPU-only build).
- **Files**: `build.bat`, `installer/setup.iss` (две конфигурации), `docs/BUILD.md`.
- **DoD**: два installer'а собираются одной командой, оба работают на чистой VM.

---

## Зависимости (граф)

```
PERF-001 (bench) ───┬──> все остальные
                    │
PERF-008 (CPU-only torch) <── PERF-001
PERF-014 (two-installer) <── PERF-008
PERF-006 (ONNX) <── PERF-001
PERF-007 (detector-skip) <── PERF-001
```

Все Tier-1 задачи независимы между собой и могут идти параллельно ПОСЛЕ `PERF-001`.

---

## Рекомендуемая первая итерация (1 рабочий день)

1. **`PERF-001`** [QA] — baseline-бенчмарк (1 день; БЛОКЕР).
2. **`PERF-003`** [OCR] — lazy-load (1–2 ч).
3. **`PERF-002`** [OCR] — downsample (½ дня).
4. **`PERF-004`** [OCR] — async-batch translate (½ дня).
5. **`PERF-005`** [Qt UI] — incremental repaint (½ дня).
6. **`PERF-001`** [QA] — повторный прогон бенчмарка → diff-отчёт.

Ожидаемая суммарная цифра после первой итерации:
- OCR (CPU): 1200 → ~500–700 мс (с downsample).
- Translation (5 блоков, cache miss): 1250 → ~350 мс.
- End-to-end (без cache hit): ~2.5 с → ~1 с.
- End-to-end (cache hit): ~600 мс → ~200 мс.
- Idle RAM: 800 → ~250 МБ.
- Cold start: 4 → ~1 сек.

---

## Что НЕ в backlog (отложено)

- **GPU runtime-детект** (`OCR-1` из штурма) — отложено до появления CUDA.
- **GPU-installer** — отложено вместе с GPU-задачами.
- **WebGPU / DirectML / OpenVINO** — не рассматривается на данном этапе.

При появлении GPU в окружении первой добавим обратно `OCR-1` (auto-detect + fallback) — это даёт ×10–20 на NVIDIA при той же кодовой базе.
