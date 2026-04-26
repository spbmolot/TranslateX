---
description: Быстрый smoke-тест установленной версии TranslateX (~30 секунд) — проверка старта, OCR-инициализации, отсутствия крашей.
---

Быстрая проверка работоспособности установленной версии. Не требует
пересборки.

## 1. Закрыть запущенные копии
// turbo
```powershell
Get-Process -Name TranslateX,python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
```

## 2. Очистить старый лог и запустить с DEBUG
// turbo
```powershell
$exe = "C:\Program Files (x86)\TranslateX\TranslateX.exe"
$log = "C:\Program Files (x86)\TranslateX\_internal\translatex.log"
Remove-Item -Force $log -ErrorAction SilentlyContinue
$env:TRANSLATEX_LOG = "DEBUG"
$p = Start-Process -FilePath $exe -PassThru
Write-Host "PID=$($p.Id), ждём 25с до OCR ready..."
Start-Sleep -Seconds 25
```

## 3. Прочитать лог и оценить
// turbo
```powershell
Get-Content $log -Encoding UTF8 | Select-Object -Last 40
```

Ожидаемые маркеры (по порядку):
- `TranslateX logging level=DEBUG`
- `Initializing EasyOCR for languages:`
- `EasyOCR ready`
- `OCR engine ready`
- `OCR-движок инициализирован`

Маркеры провала:
- `WinError 1114` → DLL CRT конфликт → передай `/pack`.
- `Qt5Core.dll … 0xc0000409` в Event Log → передай `/qa` для диагностики.
- `ModuleNotFoundError` → передай `/pack` (PyInstaller hooks).

## 4. Проверить Windows Event Log на свежие крэши
// turbo
```powershell
Get-WinEvent -FilterHashtable @{LogName='Application'; Level=1,2,3; StartTime=(Get-Date).AddMinutes(-2)} -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -match 'TranslateX|c10|torch|python' } |
  Select-Object -First 3 TimeCreated,LevelDisplayName,@{N='Msg';E={$_.Message.Substring(0,[Math]::Min(300,$_.Message.Length))}} |
  Format-List
```

## 5. Закрыть процесс
// turbo
```powershell
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
```

## Итог
Доложи: PASS / FAIL + причину + ссылки на логи.
