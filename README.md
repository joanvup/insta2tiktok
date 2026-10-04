# Insta2TikTok

Aplicación en Python 3.11+ que republica automáticamente **Reels de Instagram** en **TikTok**, usando instaloader para la extracción y la API oficial de TikTok Content Posting API v2 para la publicación.

---

## ⚠️ Aviso legal

- La extracción de datos de Instagram **sin usar su API oficial** puede ir en contra de sus [Términos de Servicio](https://help.instagram.com/581066165581870). Esta herramienta es solo para uso personal con cuentas que te pertenecen.
- **Solo republica contenido del que seas titular** o para el que tengas autorización explícita del autor.
- Los autores de este software no se responsabilizan por el mal uso de la herramienta.

---

## Arquitectura

```
app/
├── config/       # Configuración tipada con pydantic-settings
├── storage/      # Modelos SQLite (SQLModel), repositorio de Reels
├── dedup/        # Deduplicación por shortcode, SHA-256 y hash perceptual
├── instagram/    # Extractor de Reels con instaloader
├── tiktok/       # OAuth2, cliente API y publicador
├── scheduler/    # Orquestador del ciclo completo
└── cli/          # Interfaz de línea de comandos
tests/            # Pruebas unitarias con pytest
```

> **Decisión de diseño — extractor Instagram**: Se eligió `instaloader` por su soporte de sesiones/cookies, comunidad activa y no requerir API oficial. La interfaz `ReelExtractorInterface` en `app/instagram/extractor.py` permite intercambiar el backend (p.ej. yt-dlp) sin cambiar el resto de la aplicación.

---

## Requisitos

- Python 3.11+
- Cuenta TikTok for Developers con app aprobada (scopes: `video.publish`, `video.upload`)
- Cuenta pública de Instagram

---

## Instalación

```bash
git clone https://github.com/tu-usuario/insta2tiktok.git
cd insta2tiktok

# Crear entorno virtual
python -m venv .venv

# Activar (Windows)
.\.venv\Scripts\activate
# Activar (macOS / Linux)
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Instalar dependencias de desarrollo (para tests)
pip install -r requirements-dev.txt

# Configurar variables de entorno
cp .env.example .env
```

Edita `.env` con tus credenciales.

---

## Configurar la app en TikTok for Developers

1. Ve a [https://developers.tiktok.com](https://developers.tiktok.com) y crea una cuenta.
2. Crea una nueva app en **My Apps → Create app**.
3. En **Products**, activa **Content Posting API**.
4. En **Login Kit**, añade el **Redirect URI**: `http://localhost:8080/callback`.
5. Solicita los scopes: `video.publish`, `video.upload` (opcional: `video.list`).
6. Copia el **Client Key** y **Client Secret** a tu `.env`.

> **IMPORTANTE**: Hasta que tu app pase la auditoría de TikTok, los videos publicados con *Direct Post* serán **privados (`SELF_ONLY`)** independientemente de la configuración de privacidad. Solicita la auditoría desde el portal de desarrolladores cuando estés listo para publicaciones públicas.

---

## Primer uso — autenticación TikTok

```bash
python -m app auth
```

Se abrirá el navegador con la página de autorización de TikTok. Al aceptar, el token se guarda en `tiktok_token.json`. El token se refresca automáticamente antes de expirar.

---

## Comandos disponibles

| Comando | Descripción |
|---|---|
| `python -m app auth` | Obtener/renovar token OAuth de TikTok |
| `python -m app run` | Ejecutar ciclo completo una vez |
| `python -m app run --dry-run` | Simular sin descargar ni publicar |
| `python -m app status` | Ver los últimos 10 Reels registrados |
| `python -m app retry` | Reintentar Reels fallidos |
| `python -m app reset-reel <shortcode>` | Resetear estado de un Reel específico |

---

## Ejecución programada

### Con APScheduler (integrado)

El orquestador se puede llamar periódicamente con un cron job externo o con APScheduler:

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from app.scheduler.jobs import run_cycle

scheduler = BlockingScheduler()
scheduler.add_job(run_cycle, "interval", minutes=60)
scheduler.start()
```

### Con cron (Linux / macOS)

```bash
crontab -e
```

Añadir:

```
0 * * * * /ruta/al/proyecto/.venv/bin/python -m app run >> /var/log/insta2tiktok.log 2>&1
```

### Con systemd (Linux)

Crear `/etc/systemd/system/insta2tiktok.service`:

```ini
[Unit]
Description=Insta2TikTok — republica Reels en TikTok
After=network.target

[Service]
Type=oneshot
User=tu_usuario
WorkingDirectory=/ruta/al/proyecto
ExecStart=/ruta/al/proyecto/.venv/bin/python -m app run
StandardOutput=journal
StandardError=journal
```

Crear `/etc/systemd/system/insta2tiktok.timer`:

```ini
[Unit]
Description=Ejecución horaria de Insta2TikTok

[Timer]
OnCalendar=*:0/60
Persistent=true

[Install]
WantedBy=timers.target
```

Activar:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now insta2tiktok.timer
sudo systemctl status insta2tiktok.timer
```

---

## Deduplicación

La app valida duplicados en tres capas:

1. **Shortcode de Instagram** — clave única en la base de datos.
2. **Hash SHA-256 del archivo** — evita republicar contenido idéntico aunque cambie el shortcode.
3. **Hash perceptual** (opcional, `ENABLE_PERCEPTUAL_HASH=true`) — detecta re-subidas con ligeras variaciones. Requiere instalar `imagehash Pillow av`.

---

## Esquema de base de datos

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

**Estados posibles** (`status`):

| Estado | Descripción |
|---|---|
| `discovered` | Reel encontrado en Instagram, aún no descargado |
| `downloaded` | Video descargado localmente |
| `processing` | Subida a TikTok en progreso |
| `published` | Publicado exitosamente en TikTok |
| `failed` | Error en descarga o publicación (ver `fail_reason`) |
| `skipped_duplicate` | Omitido por ser duplicado |

---

## Pruebas

```bash
pytest
```

Los tests cubren: SHA-256, deduplicación por shortcode y hash, estados del repositorio, recorte de captions, validación de video, refresco de token OAuth y comandos CLI.

---

## Solución de problemas

| Problema | Solución |
|---|---|
| `No hay token de TikTok` | Ejecutar `python -m app auth` |
| `La cuenta @X es privada` | Usar solo cuentas públicas |
| Instagram devuelve 401/429 | Configurar `INSTAGRAM_SESSION_FILE` con cookies |
| Video publicado pero privado | Tu app no ha pasado auditoría TikTok — es normal |
| `Error al refrescar token` | El refresh_token expiró; ejecutar `python -m app auth` de nuevo |
| Video demasiado grande | TikTok tiene límite de 4 GB; reducir duración del Reel |
