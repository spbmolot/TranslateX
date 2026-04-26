---
description: Полная пересборка TranslateX — PyInstaller bundle + Inno Setup installer + smoke-тест.
---

Полная пересборка релиза. Каждый шаг выполняй последовательно,
проверяй вывод. Если шаг провален — останавливайся и докладывай.

## 1. Прогнать unit-тесты
// turbo
```powershell
pytest d:\CascadeProjects\TranslateX\tests -v
```

Если красные — НЕ продолжай, передай задачу `/qa`.

## 2. Очистить старый bundle
// turbo
```powershell
Remove-Item -Recurse -Force d:\CascadeProjects\TranslateX\build,d:\CascadeProjects\TranslateX\dist -ErrorAction SilentlyContinue
```

## 3. Собрать PyInstaller bundle (~25 минут)
```powershell
pyinstaller --clean --noconfirm d:\CascadeProjects\TranslateX\build.spec
```

Проверь что в `dist\TranslateX\_internal\PyQt5\Qt5\bin\` НЕТ дублей
`MSVCP140.dll` / `VCRUNTIME140.dll` (`build.spec::_strip_dup_crt`
должен был их удалить).

## 4. Smoke dev-bundle (без installer)
```powershell
$env:TRANSLATEX_LOG="DEBUG"
$exe = "d:\CascadeProjects\TranslateX\dist\TranslateX\TranslateX.exe"
$p = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 25
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Get-Content "d:\CascadeProjects\TranslateX\dist\TranslateX\_internal\translatex.log" -Encoding UTF8 | Select-Object -Last 30
```

Должно быть `OCR engine ready` без crash.

## 5. Собрать installer (~13 минут)
```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" d:\CascadeProjects\TranslateX\installer.iss
```

Артефакт: `installer_output\TranslateX_setup_*.exe`.

## 6. Поставить installer и smoke-тест
- Закрыть запущенный TranslateX.
- Запустить новый installer.
- После установки выполнить шаги из `/smoke`.

## 7. Финальный отчёт
- Версия (`__init__.py::__version__`).
- Размер bundle (`dist/TranslateX/`) и installer.
- Лог smoke-теста (последние 50 строк).
- Любые предупреждения PyInstaller.
