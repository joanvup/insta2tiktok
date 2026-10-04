# Insta2TikTok

Aplicación Python 3.11+ que republica **Reels de Instagram** en **TikTok** usando la API oficial de TikTok (Content Posting API v2). Extrae con `instaloader` (sin API oficial de Instagram), valida duplicados en tres capas y publica de forma programada.

> Documentación de TikTok verificada contra la versión **agosto 2026**.

---

## Aviso legal

- Extraer datos de Instagram **sin su API oficial puede incumplir sus Términos de Servicio**. Úsalo solo con cuentas que te pertenezcan o sobre las tengas autorización.
- **Solo republica contenido del que seas titular** o con autorización explícita del autor.
- El scraping de Instagram puede dejar de funcionar en cualquier momento sin aviso.

---

## Arquitectura

```
app/
├── config/       Configuración tipada (pydantic-settings)
├── storage/      SQLite + SQLModel, repositorio de Reels
├── dedup/        Deduplicación: shortcode, SHA-256, hash perceptual
├── instagram/    Extractor de Reels (instaloader)
├── tiktok/
│   ├── auth.py       OAuth 2.0 con callback local y modo manual
│   ├── client.py     Cliente HTTP de la Content Posting API
│   ├── chunking.py   Planificador de subida por chunks
│   ├── validator.py  Validación local con ffprobe
│   └── publisher.py  Orquestación de la publicación
├── scheduler/    Ciclo completo (orquestador)
└── cli/          Interfaz de línea de comandos
tests/            Pruebas unitarias
```

**Decisiones clave:**

| Decisión | Motivo |
|---|---|
| `instaloader` sobre `yt-dlp` | Soporte nativo de sesiones/cookies (evita rate-limiting), API Python limpia para iterar posts, comunidad activa. La interfaz `ReelExtractorInterface` permite cambiarlo sin tocar el resto. |
| Content Posting API v2 Direct Post | Es la única vía oficial para publicar en la cuenta del usuario. |
| `FILE_UPLOAD` sobre `PULL_FROM_URL` | Los videos se descargan de Instagram; `PULL_FROM_URL` exigiría un dominio verificado por TikTok. |
| Pre-validación con `ffprobe` | Los `fail_reason` de TikTok (`duration_check_failed`, etc.) solo se obtienen **después** de subir el video completo. Validar localmente evita gastar cuota. |
| Chunking calculado una vez | El plan de chunks se usa tanto para el payload de `/init/` como para los `PUT`, garantizando que nunca se desincronicen. |

---

## Requisitos

- Python 3.11+
- **FFmpeg** (para `ffprobe`, validación local)
- Cuenta TikTok for Developers con Content Posting API

Instalar FFmpeg:

```bash
# Windows (winget)
winget install Gyan.FFmpeg

# macOS
brew install ffmpeg

# Debian/Ubuntu
sudo apt install ffmpeg
```

---

## Instalación

```bash
git clone <tu-repo>
cd insta2tiktok

python -m venv .venv

# Windows
.\.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
pip install -r requirements-dev.txt   # solo para tests

# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

---

## Crear la app en TikTok for Developers

### 1. Cuenta
1. Regístrate en [developers.tiktok.com](https://developers.tiktok.com/). Usa **email corporativo**; Gmail personal suele ser rechazado en la revisión.
2. Completa nombre, país y razón social.

### 2. Crear la app
**My Apps → Create app**

| Campo | Valor |
|---|---|
| App name | `insta2tiktok` (no es público) |
| Client Type | **Web** (OAuth por navegador, como implementa `auth.py`) |
| Products | **Content Posting API** + **Login Kit** |

### 3. Activar Direct Post
Dentro de **Content Posting API** activa el interruptor de **Direct Post**. Sin esto tu app puede dejar borradores en la bandeja del usuario pero no publicar en su perfil.

### 4. Redirect URI — **HTTPS obligatorio**
TikTok es explícito: *"URIs must be absolute and begin with `https`"*.

| Regla | Valor |
|---|---|
| Esquema | **Debe empezar por `https`** |
| Parámetros | Prohibidos |
| Fragmento `#` | Prohibido |
| Longitud | < 512 caracteres |
| Máximo | 10 URIs por app |

Para desarrollo local, `http://localhost` será rechazado. Usa **ngrok**:

```bash
ngrok http 8080
```

Registra la URL `https://` resultante (sin barra final) como Redirect URI y ponla en `.env`:

```dotenv
TIKTOK_REDIRECT_URI=https://a1b2c3d4.ngrok-free.app
TIKTOK_CALLBACK_HOST=127.0.0.1
TIKTOK_CALLBACK_PORT=8080
```

El flujo queda: TikTok → `https://a1b2c3d4.ngrok-free.app` → ngrok → `127.0.0.1:8080`.

En producción usa un dominio propio con TLS:

```dotenv
TIKTOK_REDIRECT_URI=https://miapp.example.com/tiktok/callback
```

### 5. Solicitar el scope
**Scopes → Add Scopes → `video.publish`**, y después solicitar la **aprobación**.

| Scope | ¿Necesario? |
|---|---|
| `video.publish` | **Sí**, para Direct Post |
| `video.upload` | No, es de la API legacy |
| `video.list` | **No existe en la API v2 actual** |

> **Corrección sobre el diseño original:** el requisito de contrastar contra `video.list` no es implementable con la API oficial v2 — no expone listado de videos del creador. La deduplicación efectiva queda en SQLite (shortcode + SHA-256 + opcional perceptual).

### 6. Credenciales
En **Manage apps**: **Client Key** y **Client Secret**.

### 7. Verificar permisos reales
```bash
python -c "from app.tiktok.client import TikTokClient; import json; print(json.dumps(TikTokClient().query_creator_info(), indent=2, ensure_ascii=False))"
```

- Si `privacy_level_options` solo contiene `["SELF_ONLY"]` → **no tienes auditoría**, todo se publica en privado.
- Si incluye `PUBLIC_TO_EVERYONE` → tu app ya pasó la revisión.

---

## Auditoría

TikTok: *"All content posted by unaudited clients will be restricted to private viewing mode"*.

No hay forma de evitarlo desde código; es una restricción del lado de TikTok. Se solicita en [developers.tiktok.com/application/content-posting-api](https://developers.tiktok.com/application/content-posting-api) y normalmente exige app funcional, cuenta TikTok con contenido, política de privacidad y prueba de que el contenido es tuyo.

Mientras tanto, el código **falla de forma segura**: si pides `PUBLIC_TO_EVERYONE` y no está permitido, `resolve_privacy_level()` cae a `SELF_ONLY` automáticamente en lugar de recibir `privacy_level_option_mismatch`.

---

## Uso

```bash
python -m app auth            # login TikTok (abre navegador)
python -m app auth --manual   # servidor headless: pega el código a mano
python -m app run             # un ciclo
python -m app run --dry-run   # simula sin descargar ni publicar
python -m app status          # últimos 10 registros
python -m app retry           # reintenta fallidos
python -m app reset-reel ABC  # resetea un Reel
```

### Primera vez
```bash
python -m app auth
python -m app run --dry-run   # comprueba que la extracción funciona
python -m app run
```

---

## Automatización

### APScheduler
```python
from apscheduler.schedulers.blocking import BlockingScheduler
from app.scheduler.jobs import run_cycle

scheduler = BlockingScheduler()
scheduler.add_job(run_cycle, "interval", minutes=60)
scheduler.start()
```

### cron
```cron
0 * * * * /ruta/al/proyecto/.venv/bin/python -m app run >> /var/log/insta2tiktok.log 2>&1
```

### systemd
`/etc/systemd/system/insta2tiktok.service`:
```ini
[Unit]
Description=Insta2TikTok
After=network.target

[Service]
Type=oneshot
User=tu_usuario
WorkingDirectory=/ruta/al/proyecto
ExecStart=/ruta/al/proyecto/.venv/bin/python -m app run
```

`/etc/systemd/system/insta2tiktok.timer`:
```ini
[Unit]
Description=Insta2TikTok cada hora

[Timer]
OnCalendar=*:0/60
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now insta2tiktok.timer
```

---

## Deduplicación

1. **Shortcode de Instagram** — clave única en la base de datos.
2. **SHA-256 del archivo** — detecta contenido idéntico aunque cambie el shortcode.
3. **Hash perceptual** (opcional) — detecta re-subidas con ligeras variaciones. Requiere `pip install imagehash Pillow av`.

Estados: `discovered`, `downloaded`, `processing`, `published`, `failed`, `skipped_duplicate`.

---

## Esquema SQL

```sql
CREATE TABLE reels (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    shortcode           TEXT UNIQUE NOT NULL,
    video_url           TEXT,
    video_path          TEXT,
    video_hash          TEXT,
    perceptual_hash     TEXT,
    caption             TEXT,
    instagram_timestamp DATETIME,
    duration            REAL,
    status              TEXT NOT NULL DEFAULT 'discovered',
    fail_reason         TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    tiktok_publish_id   TEXT,
    created_at          DATETIME NOT NULL,
    updated_at          DATETIME NOT NULL,
    published_at        DATETIME
);
```

---

## Límites verificados de TikTok

| Límite | Valor | Validado localmente |
|---|---|---|
| Tamaño máximo | 4 GB | Sí |
| Chunk mínimo | 5 MB | Sí (`chunking.py`) |
| Chunk máximo | 64 MB | Sí |
| Último chunk | hasta 128 MB | Sí |
| Chunks máximos | 1000 | Sí |
| `total_chunk_count` | `video_size // chunk_size` (floor) | Sí |
| Resolución | 360×360 – 4096×4096 | Sí |
| FPS | 23 – 60 | Sí |
| Codecs | H.264, H.265, VP8, VP9 | Sí |
| Duración | 600 s por API; `max_video_post_duration_sec` por cuenta | Sí |
| Caption | 2200 **runes UTF-16** (no caracteres) | Sí |
| Rate limit init | 6 req/min por token | Backoff |
| Rate limit status | 30 req/min por token | Backoff |

> El cálculo de chunks sigue el ejemplo oficial: un archivo de 50.000.123 bytes con chunks de 10 MB produce **5** chunks (4×10 MB + 10.000.123 B), no 6. El último absorbe los bytes sobrantes porque **cada chunk debe tener mínimo 5 MB**.

### `fail_reason` que devuelve la API

| Motivo | Significado |
|---|---|
| `file_format_check_failed` | Formato no soportado |
| `duration_check_failed` | Duración fuera de rango |
| `frame_rate_check_failed` | FPS fuera de rango |
| `picture_size_check_failed` | Resolución fuera de rango |
| `spam_risk_too_many_posts` | Cuota diaria alcanzada |
| `auth_removed` | El usuario revocó el acceso (no reintentar) |
| `spam_risk_*` | Riesgo de spam/bloqueo (no reintentar) |

`client.py` distingue los reintentables de los definitivos mediante `TikTokAPIError.retryable`.

---

## Tests

```bash
pytest
```

Cubren: SHA-256, deduplicación por shortcode y hash, máquina de estados del repositorio, planificador de chunks (incluido el ejemplo oficial de TikTok), iteración de chunks con verificación de suma, `Content-Type` por extensión, recorte de caption en UTF-16 con emoji, validación de duración/resolución/FPS/codec, refresco de token, polling de `publish_id` y resolución de privacidad.

---

## Solución de problemas

| Problema | Causa / Solución |
|---|---|
| `No hay token de TikTok` | Ejecuta `python -m app auth` |
| `state no coincide` en el callback | La URL de ngrok cambió. Re-registra el redirect URI |
| `OAuth fallido: access_token_invalid` | El `code` expiró (vida ~5 min). Repite `auth` |
| `privacy_level_option_mismatch` | El código lo evita, pero si lo ves: tu app no tiene auditoría |
| `unaudited_client_can_only_post_to_private_account` | Tu app no pasó la auditoría. Usa `SELF_ONLY` |
| `spam_risk_too_many_posts` | Cuota diaria. Baja `MAX_DAILY_PUBLISHES` |
| `duration_check_failed` | El video excede `max_video_post_duration_sec` de tu cuenta |
| `ffprobe no encontrado` | Instala FFmpeg o ajusta `FFPROBE_PATH` (no bloquea, solo omite la validación) |
| `Error al refrescar token` | El `refresh_token` expiró. Ejecuta `auth` de nuevo |
| Instagram devuelve 401/429 | Configura `INSTAGRAM_SESSION_FILE` y sube los delays |
| `La cuenta @X es privada` | Solo se admiten cuentas públicas |