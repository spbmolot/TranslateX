# Code-signing для TranslateX (PKG-2)

> **Цель:** убрать предупреждение Windows SmartScreen «Неизвестный издатель»
> при первом запуске installer'а и `.exe` у конечного пользователя.

Без подписи Windows показывает оранжевый/синий screen вида:

> «Система Windows защитила ваш компьютер. Запуск **этого приложения** заблокирован, поскольку его издатель не проверен. Издатель: Неизвестно.»

После подписи **доверенным** сертификатом издатель отображается явно
(`Publisher: TranslateX`), а после набора репутации (для OV) или сразу
(для EV) предупреждение исчезает совсем.

---

## 1. Что подписывать

В TranslateX два артефакта, каждый из которых требует подписи **отдельно**:

| Артефакт | Путь | Когда подписывается |
|----------|------|----------------------|
| Основной `.exe` | `dist\TranslateX\TranslateX.exe` | После шага PyInstaller, **до** Inno Setup |
| Установщик | `installer\Output\TranslateX_Setup_<ver>.exe` | После Inno Setup |

⚠ Порядок важен: если подписать `.exe` ПОСЛЕ упаковки installer'ом, его
подпись не попадёт в готовый дистрибутив (Inno Setup упаковывает байт-в-байт).

---

## 2. Какой сертификат покупать

| Тип | Цена/год (2026) | SmartScreen | Где взять |
|-----|-----------------|-------------|-----------|
| **EV (Extended Validation)** | $300–600 | **Сразу** убирает warning. Хранится только на USB-токене (eToken). | Sectigo, DigiCert, GlobalSign |
| **OV (Organization Validation)** | $80–250 | Накапливает репутацию (300–3000 первых установок). После порога предупреждение исчезает. | SSL.com, Certum, Comodo (Sectigo), KSign |
| **IV (Individual)** | $50–100 | То же что OV, но для физлиц. | SSL.com, Certum |

> Для одиночного разработчика обычно достаточно **OV** с накоплением
> репутации, либо отдельно для критичной первой установки — **EV**.

После покупки получишь:
- **EV** — физический USB-токен с PIN-кодом. Сертификат не извлекаемый.
- **OV/IV** — `.pfx`/`.p12` файл + пароль (импортируется в Cert Store).

---

## 3. Установка инструментов

`signtool.exe` входит в **Windows SDK** (бесплатно):

1. Скачать [Windows 10/11 SDK](https://developer.microsoft.com/windows/downloads/windows-sdk/).
2. При установке отметить: **Windows SDK Signing Tools for Desktop Apps**.
3. Путь по умолчанию:
   `C:\Program Files (x86)\Windows Kits\10\bin\<version>\x64\signtool.exe`.
4. Добавить в `PATH` или вызывать по полному пути.

Проверка:
```powershell
signtool.exe /?
```

---

## 4. Подпись через `.pfx` (OV/IV)

### 4.1. Подпись TranslateX.exe (после PyInstaller)

```powershell
signtool sign `
  /f   "C:\path\to\cert.pfx" `
  /p   "PFX_PASSWORD" `
  /fd  SHA256 `
  /tr  "http://timestamp.digicert.com" `
  /td  SHA256 `
  /d   "TranslateX" `
  /du  "https://github.com/your-repo/translatex" `
  "dist\TranslateX\TranslateX.exe"
```

Расшифровка ключей:
- `/f` — путь к `.pfx`.
- `/p` — пароль от `.pfx`.
- `/fd SHA256` — алгоритм подписи файла (SHA-1 deprecated, **обязательно SHA-256**).
- `/tr` — RFC 3161 timestamp-сервер (бесплатные: `digicert.com`, `sectigo.com`, `globalsign.com`). Без timestamp подпись «протухает» с истечением сертификата.
- `/td SHA256` — алгоритм timestamp.
- `/d` — описание (увидит пользователь в UAC).
- `/du` — URL описания.

### 4.2. Подпись установщика (после Inno Setup)

Та же команда, но другой путь:
```powershell
signtool sign /f "..." /p "..." /fd SHA256 /tr "..." /td SHA256 `
  /d "TranslateX Installer" `
  "installer\Output\TranslateX_Setup_1.0.0.exe"
```

### 4.3. Verify

```powershell
signtool verify /pa /v "dist\TranslateX\TranslateX.exe"
```

Должно быть:
```
Successfully verified: dist\TranslateX\TranslateX.exe
```

---

## 5. Подпись через Cert Store (EV-токен)

EV-сертификат с USB-токеном **нельзя** подписать через `/f`/`/p`. Вместо
этого — Cert Store + thumbprint:

### 5.1. Узнать thumbprint

```powershell
Get-ChildItem -Path Cert:\CurrentUser\My |
  Where-Object { $_.Subject -match "TranslateX" } |
  Select-Object Subject, Thumbprint
```

### 5.2. Подпись

```powershell
signtool sign `
  /sha1 "ABCD1234EF5678..." `
  /fd   SHA256 `
  /tr   "http://timestamp.digicert.com" `
  /td   SHA256 `
  "dist\TranslateX\TranslateX.exe"
```

При первой подписи токен запросит PIN. Для batch-режима:
- Sectigo/DigiCert поддерживают PIN-кеш на сессию (через SafeNet Authentication Client → Tools → ☑ Single Logon).
- В CI/CD используй HSM-as-a-Service (например, Azure Key Vault + AzureSignTool).

---

## 6. Интеграция в текущий build pipeline TranslateX

### 6.1. Через `build.bat`

В будущем можно вставить два шага между Step 3 (PyInstaller) и Step 4
(Inno Setup), и после Step 4. Минимальный draft (НЕ применять без cert):

```batch
:: ── Step 3.5: Sign TranslateX.exe ────────────────────────────
if defined SIGN_CERT_PATH (
    echo [STEP 3.5/4] Signing TranslateX.exe...
    signtool sign /f "%SIGN_CERT_PATH%" /p "%SIGN_CERT_PASSWORD%" ^
      /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
      /d "TranslateX" "dist\TranslateX\TranslateX.exe"
    if %errorlevel% neq 0 ( echo [ERROR] Sign failed. & goto :fail )
) else (
    echo [WARN] SIGN_CERT_PATH not set — skipping code-signing.
)

:: ── Step 5: Sign installer (после Inno Setup) ────────────────
if defined SIGN_CERT_PATH (
    signtool sign /f "%SIGN_CERT_PATH%" /p "%SIGN_CERT_PASSWORD%" ^
      /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
      /d "TranslateX Installer" ^
      "installer\Output\TranslateX_Setup_%APP_VERSION%.exe"
)
```

Использование:
```powershell
$env:SIGN_CERT_PATH     = "C:\secure\cert.pfx"
$env:SIGN_CERT_PASSWORD = "..."
.\build.bat
```

### 6.2. Через `setup.iss` (Inno Setup сам подпишет)

Inno Setup имеет встроенную поддержку через директиву `SignTool`. Шаги:

1. Зарегистрировать tool в Inno Setup (один раз, через `Tools → Configure Sign Tools`):
   ```
   Name:    signtool
   Command: signtool.exe sign /f $qC:\cert.pfx$q /p $q$pPASSWORD$q$q /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $f
   ```
   `$f` будет заменён Inno Setup'ом на путь файла, `$q` = `"`, `$p` = pause.

2. В `setup.iss` добавить:
   ```ini
   [Setup]
   SignTool=signtool
   SignedUninstaller=yes
   ```
   Это подпишет **и сам installer, и его внутренний uninstaller**.

3. Если хочешь подписать вложенный `TranslateX.exe` через Inno Setup
   (вместо ручного `signtool` после PyInstaller):
   ```ini
   [Files]
   Source: "{#BuildDir}\TranslateX.exe"; DestDir: "{app}"; Flags: ignoreversion sign
   ```
   Флаг `sign` заставит Inno Setup пропустить файл через `SignTool=`
   ДО упаковки. Удобно.

⚠ Если ты используешь EV-токен — `Configure Sign Tools` всё равно требует
указать команду `signtool.exe sign /sha1 ABCD... /fd SHA256 /tr ... /td SHA256 $f`.
PIN запрашивается при компиляции; кэшируется через SafeNet Single Logon.

---

## 7. Проверка результата

### 7.1. Свойства файла (Explorer)

ПКМ по `TranslateX.exe` → **Свойства** → вкладка **Цифровые подписи**.
Должно быть:
- Имя подписавшего: `TranslateX` (или название твоего юр.лица)
- Алгоритм дайджеста: `sha256`
- Метка времени: дата подписи

### 7.2. SmartScreen reputation

Загрузить установщик на чистую Win10/11 VM (Hyper-V/VirtualBox) и
запустить. Что должно произойти:

| Сертификат | Поведение |
|------------|-----------|
| **EV** | UAC сразу с твоим именем издателя. SmartScreen не показывается. |
| **OV (только что куплен)** | SmartScreen всё ещё может предупреждать первые ~50–500 установок, но Publisher уже виден. После репутации — пропадает. |
| **OV (репутация набрана)** | Прозрачная установка, как у крупных вендоров. |
| **Self-signed** | Предупреждение остаётся (Windows не доверяет твоему root CA). Полезно только для тестирования pipeline. |

### 7.3. Online проверка репутации

Microsoft не публикует статус reputation API публично, но можно отслеживать:
- **Edge Browser** Downloads — он покажет «не часто скачивается» пока репутация низкая.
- VirusTotal — подгружает в свою выборку и параллельно проверяет 70+ AV-вендоров.
- [signserver.org/test](https://signserver.org/) — внешняя проверка валидности подписи.

---

## 8. Self-signed для dev/тестов (опционально)

Создать сертификат для проверки самого pipeline (НЕ устранит SmartScreen):

```powershell
$cert = New-SelfSignedCertificate `
  -Type CodeSigningCert `
  -Subject "CN=TranslateX-Dev" `
  -KeyAlgorithm RSA -KeyLength 2048 `
  -KeyUsage DigitalSignature `
  -CertStoreLocation Cert:\CurrentUser\My `
  -NotAfter (Get-Date).AddYears(3)

# Экспорт в .pfx
$pwd = ConvertTo-SecureString -String "test123" -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath ".\dev-cert.pfx" -Password $pwd

# (опц.) импорт в Trusted Publishers — чтобы локально не ругался:
Import-PfxCertificate -FilePath ".\dev-cert.pfx" `
  -Password $pwd `
  -CertStoreLocation Cert:\CurrentUser\TrustedPublisher
```

Использование:
```powershell
signtool sign /f .\dev-cert.pfx /p test123 /fd SHA256 `
  "dist\TranslateX\TranslateX.exe"
```

⚠ Никогда не коммить `dev-cert.pfx` в git — добавь в `.gitignore`.

---

## 9. Чеклист «пошёл подписывать»

- [ ] Куплен сертификат у CA (Sectigo / DigiCert / SSL.com / Certum).
- [ ] Установлен Windows SDK Signing Tools (`signtool.exe` в PATH).
- [ ] `.pfx` лежит вне репо (`C:\secure\` или подобное).
- [ ] Добавлены `*.pfx` и `*.p12` в `.gitignore`.
- [ ] Сертификат **не SHA-1** (Windows блокирует с июля 2020).
- [ ] Подпись `TranslateX.exe` ДО Inno Setup.
- [ ] Подпись installer'а ПОСЛЕ Inno Setup.
- [ ] Включён `SignedUninstaller=yes` в `setup.iss`.
- [ ] Использован RFC 3161 timestamp (`/tr` + `/td SHA256`).
- [ ] Проверено `signtool verify /pa /v` на обоих файлах.
- [ ] Проверена UAC-картинка на чистой Win10 VM.

---

## 10. Полезные ссылки

- [signtool reference (MS Docs)](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool)
- [Inno Setup signing docs](https://jrsoftware.org/ishelp/index.php?topic=setup_signtool)
- [SmartScreen submission portal](https://www.microsoft.com/en-us/wdsi/filesubmission) — отправить дистрибутив на ускоренное накопление репутации.
- [SSL.com price guide](https://www.ssl.com/certificates/code-signing/) — самые дешёвые EV для одиночек.
- [AzureSignTool](https://github.com/vcsjones/AzureSignTool) — подпись из Azure Key Vault для CI/CD без локального токена.
