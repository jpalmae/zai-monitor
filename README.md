# zai-monitor

Monitor en tiempo real de las cuotas del **GLM Coding Plan** de Z.ai:
**5 Hours Quota**, **Weekly Quota** y **Web Search / Reader / Zread**.

Incluye una **TUI** (interfaz en terminal) con barras de progreso, countdown al
reset y sparkline de historial, más un **daemon de alertas por Telegram** que te
avisa cuando pasas ciertos umbrales de consumo.

![status](https://img.shields.io/badge/status-funcionando-brightgreen) ![python](https://img.shields.io/badge/python-3.10%2B-blue)

---

## Tabla de contenidos

1. [Requisitos](#requisitos)
2. [Instalación](#instalación)
3. [Conseguir tu API key](#conseguir-tu-api-key)
4. [Uso](#uso)
5. [Alertas por Telegram](#alertas-por-telegram)
6. [Daemon en background (macOS)](#daemon-en-background-macos)
7. [Configuración](#configuración)
8. [Solución de problemas](#solución-de-problemas)
9. [Cómo funciona (notas técnicas)](#cómo-funciona-notas-técnicas)
10. [Estructura del proyecto](#estructura-del-proyecto)

---

## Requisitos

- **Python 3.10+** (`python3 --version`)
- Una **suscripción activa** al GLM Coding Plan de Z.ai (Lite / Pro / Max)
- macOS, Linux o WSL. La TUI necesita una terminal con color.
- (Opcional) Una cuenta de Telegram para las alertas

## Instalación

```bash
# 1. Entra al proyecto
cd zai-monitor

# 2. Crea y activa un entorno virtual
python3 -m venv .venv
source .venv/bin/activate      # en Windows: .venv\Scripts\activate

# 3. Instala dependencias (TUI + alertas + lint)
pip install -e ".[tui,telegram,dev]" httpx python-dotenv

# 4. Copia el archivo de entorno y añade tu API key (siguiente sección)
cp .env.example .env
```

## Conseguir tu API key

La API key del coding plan es la **misma** que usas en Claude Code / Cline /
opencode para conectarte a Z.ai. Hay 3 formas de obtenerla:

**Opción A — Desde el dashboard de Z.ai (recomendada)**
1. Entra a https://z.ai/manage-apikey/apikey-list
2. Copia una de tus claves (formato `xxxxxxxxxxxx.yyyyyyyyyyyyyyyy`)

**Opción B — Desde opencode (si lo usas)**
```bash
cat ~/.local/share/opencode/auth.json
# busca "zai-coding-plan" o "zhipuai-coding-plan" -> copia el valor de "key"
```

**Opción C — Desde Claude Code**
Revisa `~/.claude/settings.json` o tus variables de entorno (`ZAI_API_KEY`,
`ANTHROPIC_API_KEY` con el base URL de z.ai).

Una vez tengas la key, pégala en `.env`:
```bash
ZAI_API_KEY=tu-api-key-aqui
```

Verifica que funciona:
```bash
python fetcher.py
# debe imprimir tu plan y los % de cada cuota
```

---

## Uso

> Recuerda activar el entorno virtual primero: `source .venv/bin/activate`

### 1. TUI en vivo (uso principal)

```bash
python tui.py
```

Verás algo así:
```
╭──────────────────────────────────────────────────────────────────────────╮
│ z.ai GLM Coding Plan — MAX   tu@email.com                                │
│ last updated 17:03:45                                                    │
╰──────────────────────────────────────────────────────────────────────────╯
  5 Hours Quota
    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  1%
    resets in 0h 56m 48s
    at Fri Jun 19 18:00
  Weekly Quota
    ████░░░░░░░░░░░░░░░░░░░░░░░░░░  14%
    resets in 2d 18h 55m
    at Mon Jun 22 11:59
  Web Search / Reader / Zread
    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0%   [0/4000 Times]
```

**Teclas:**
| Tecla | Acción |
|-------|--------|
| `r` | Refrescar ahora |
| `q` | Salir |
| `^p` | Paleta de comandos |

**Colores de las barras** (según % consumido):
- 🟢 verde: < 50%
- 🟡 amarillo: 50–75%
- 🟠 naranja: 75–90%
- 🔴 rojo: ≥ 90%

Se auto-actualiza cada 60s (configurable) y guarda historial en `store.db` para
dibujar un sparkline debajo de cada barra cuando haya suficientes muestras.

### 2. Lectura puntual (CLI, sin TUI)

```bash
python fetcher.py
```
Útil para scripts, cron, o revisar rápido desde SSH.

### 3. Daemon de alertas (foreground)

```bash
python alerts.py
```
Hace polling cada 5 min y envía alertas a Telegram si configuras las credenciales
(ver sección [Alertas por Telegram](#alertas-por-telegram)). Sin Telegram
configurado, solo loguea a consola.

---

## Alertas por Telegram

El daemon te avisa cuando una cuota cruza ciertos umbrales de consumo. Para
activarlo necesitas un bot de Telegram.

### Paso 1 — Crear el bot (una sola vez)

1. Abre [@BotFather](https://t.me/BotFather) en Telegram
2. Envía `/newbot` → elige un nombre y un username (debe terminar en `bot`)
3. BotFather te da un **token** con este formato: `123456789:ABCdefGhi...`
4. Cópialo

### Paso 2 — Configurar el chat (asistido)

El proyecto incluye un helper que **auto-detecta tu chat id** (no tienes que
buscarlo a mano):

```bash
python setup_telegram.py TU:TOKEN_AQUI
```
El script:
1. Valida el token
2. Te pide que abras Telegram y mandes `/start` a tu bot
3. Detecta tu chat id automáticamente
3. Envía un mensaje de prueba
4. Escribe todo en `config.toml`

### Paso 3 — (Alternativa) configuración manual

Si prefieres hacerlo a mano, edita `config.toml`:

```toml
[alerts]
tg_bot_token  = "123456789:ABCdefGhi..."
tg_chat_id    = "9876543210"          # tu chat id
thresholds        = [50, 75, 90, 100] # % en los que alerta (al subir)
recovered_below   = 20                # avisa "reset/available" al bajar de esto
min_interval_sec  = 60                # anti-spam: mín. segundos entre alertas
```

Para saber tu chat id manualmente: mándale cualquier mensaje a tu bot y abre
`https://api.telegram.org/bot<TOKEN>/getUpdates` en el navegador → busca
`chat.id`.

### Cómo funciona el debounce (anti-spam)

Cada umbral dispara **una sola vez por ciclo de reset**. Ejemplo con
`thresholds = [50, 75, 90, 100]`:

- Al llegar al **50%** → avisa una vez
- Al llegar al **75%** → avisa una vez
- Si baja y vuelve a subir al 50% en el mismo ciclo → **NO** repite
- Cuando la cuota **resetea** (el `nextResetTime` cambia) → avisa "recovered" y
  se re-arma todo para el nuevo ciclo

El estado se persiste en `store.db`, así que sobrevive reinicios.

---

## Daemon en background (macOS)

Para que las alertas corran siempre (aunque cierres la terminal), usa launchd:

```bash
# 1. Edita launchd/ai.zai-monitor.alerts.plist y ajusta las rutas a tu home
#    (las líneas <string>/Users/TU_USUARIO/...</string>)

# 2. Instálalo
cp launchd/ai.zai-monitor.alerts.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai.zai-monitor.alerts.plist
```

**Logs:** `~/Library/Logs/zai-monitor-alerts.log`

**Gestión:**
```bash
# ver estado
launchctl list | grep zai-monitor

# ver logs en vivo
tail -f ~/Library/Logs/zai-monitor-alerts.log

# parar
launchctl unload ~/Library/LaunchAgents/ai.zai-monitor.alerts.plist

# reiniciar (tras cambiar config.toml)
launchctl unload ~/Library/LaunchAgents/ai.zai-monitor.alerts.plist
launchctl load   ~/Library/LaunchAgents/ai.zai-monitor.alerts.plist
```

> En Linux usa systemd o cron. Ejemplo de cron (cada 5 min):
> `*/5 * * * * cd /ruta/zai-monitor && .venv/bin/python alerts.py >> /tmp/zai.log 2>&1`

---

## Configuración

Todo lo configurable está en `config.toml`:

```toml
[monitor]
poll_interval = 300        # segundos entre polls del daemon de alertas

[tui]
refresh_interval = 60      # segundos entre refresh de la TUI

[alerts]
tg_bot_token   = ""        # token de @BotFather (vacío = desactivado)
tg_chat_id     = ""        # tu chat id
thresholds       = [50, 75, 90, 100]
recovered_below  = 20
min_interval_sec = 60
```

La API key va en `.env` (no en `config.toml`, para no commitearla por error):
```bash
ZAI_API_KEY=tu-api-key-aqui
```

---

## Solución de problemas

**`ZaiError: API error code=1001 msg='Authentication...'`**
Tu API key es inválida o caducó. Verifícala en https://z.ai/manage-apikey/apikey-list
y actualiza `.env`.

**`ZaiError: API error code=... msg='APIKey not allow access'`**
Tu key no tiene acceso a ese endpoint. Asegúrate de usar la key del **coding
plan**, no una key genérica de la API de pago por uso.

**La TUI muestra `loading…` y no avanza**
Revisa que tienes red y que `python fetcher.py` funciona. Si hay un error de red,
el estado mostrará `[red]error: ...[/]`.

**`StoreError` / problemas con sqlite**
Borra la base local y vuelve a empezar: `rm store.db*`

**El daemon de launchd no arranca**
1. Verifica las rutas en el `.plist` (`python` y `alerts.py`)
2. Revisa el log: `cat ~/Library/Logs/zai-monitor-alerts.log`
3. Prueba a mano primero: `cd zai-monitor && .venv/bin/python alerts.py`

**Telegram no envía mensajes**
1. Confirma que mandaste `/start` al bot al menos una vez (Telegram bloquea bots
   hasta que el usuario inicia la conversación)
2. Verifica token y chat_id en `config.toml`
3. El daemon loguea el mensaje que enviaría si no hay Telegram configurado

---

## Cómo funciona (notas técnicas)

Z.ai **no expone una API pública** de cuotas. Pero el dashboard web consulta un
endpoint interno que **acepta la misma API key del coding plan** como Bearer:

```
GET https://api.z.ai/api/monitor/usage/quota/limit
Authorization: Bearer <tu-coding-plan-api-key>
```

Este endpoint se descubrió inspeccionando el bundle JavaScript del dashboard
(`z.ai/manage-apikey/coding-plan/personal/usage`). Respuesta relevante:

```json
{ "data": {
    "level": "max",
    "limits": [
      { "type": "TOKENS_LIMIT", "unit": 3, "percentage": 17, "nextResetTime": 1781... },
      { "type": "TOKENS_LIMIT", "unit": 6, "percentage": 11, "nextResetTime": 1782... },
      { "type": "TIME_LIMIT",   "unit": 5, "usage": 4000, "currentValue": 0,
        "remaining": 4000, "percentage": 0, "nextResetTime": 1783... }
    ]
}}
```

Mapping `unit/type → cuota` (confirmado del bundle):
| type | unit | cuota | ¿tiene conteo absoluto? |
|------|------|-------|-------------------------|
| `TOKENS_LIMIT` | 3 | **5 Hours Quota** | no (solo %) |
| `TOKENS_LIMIT` | 6 | **Weekly Quota** | no (solo %) |
| `TIME_LIMIT` | 5 | Web Search / Reader / Zread | sí (`used/total`) |

> ⚠️ **Advertencias**
> - El endpoint **no está documentado** y puede cambiar sin aviso.
> - Es un `GET` de **solo lectura** y **no consume prompts**.
> - La cuota de tokens solo expone **porcentaje** (igual que el dashboard).
> - El `percentage` ya incluye el multiplicador x2/x3 de peak hours
>   (14:00–18:00 UTC+8).

---

## Estructura del proyecto

```
zai-monitor/
├── tui.py              # TUI con textual (barras + countdown + sparkline)
├── fetcher.py          # llama al endpoint y parsea la respuesta
├── alerts.py           # daemon de alertas con debounce por ciclo
├── store.py            # sqlite: historial + estado de alertas
├── config.py           # lee config.toml / .env
├── config.toml         # umbrales, intervalos, telegram
├── setup_telegram.py   # helper para configurar Telegram (auto-detecta chat_id)
├── probe.py            # script de discovery (referencia, no se necesita)
├── .env / .env.example # API key (no commitear .env)
├── launchd/
│   └── ai.zai-monitor.alerts.plist   # daemon para macOS
└── store.db            # sqlite autogenerado (historial + debounce)
```

## Licencia

Uso personal. Z.ai y GLM son marcas de Zhipu AI. Este proyecto no está afiliado
a Z.ai y solo lee datos que el dashboard web ya expone.
