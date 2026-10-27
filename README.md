# insta2tiktok

Aplicación local para cargar los últimos reels de una cuenta pública de Instagram,
elegir cuáles publicar y enviarlos a una cuenta de TikTok con su descripción.

- Interfaz web en español, accesible solo desde `http://127.0.0.1:8000`.
- Eliges la cuenta de Instagram y la cantidad de reels (hasta 30 por carga).
- Editas la descripción de TikTok (máximo 2200 caracteres) por cada reel.
- La publicación es automática mediante un navegador Chromium controlado con
  Playwright, después de iniciar sesión manualmente una sola vez en cada plataforma.
- Cola de publicación secuencial, historial, reintentos y protección contra duplicados.

> **Aviso:** publica únicamente contenido propio o para el que tengas autorización.
> Automatizar TikTok puede infringir sus Términos de Servicio y provocar restricciones
> o el cierre de la cuenta. La aplicación no guarda contraseñas y se detiene antes de
> publicar si no puede confirmar que el video será público. Úsala bajo tu responsabilidad.

## Requisitos

- Windows 10 u 11.
- Python 3.11 o superior.
- Una cuenta pública de Instagram (con sesión iniciada) y una cuenta de TikTok donde
  publicar.
- Conexión a internet.
- No se necesita Node, Docker ni ffmpeg.

## Instalación

1. Ejecuta `setup.bat` una vez. Crea `.venv`, instala las dependencias y descarga
   Chromium para Playwright.
2. Ejecuta `run.bat` para iniciar la aplicación y abrirla en el navegador.

También puedes iniciar el servidor manualmente:

```bat
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Uso

1. **Conectar Instagram.** Pulsa «Conectar Instagram» y completa el inicio de sesión en
   la ventana de Chromium. La aplicación detecta la cookie `sessionid`, valida la sesión
   y la guarda en `data/sessions/`. Como alternativa, puedes pegar la cookie
   `sessionid` manualmente desde la interfaz.
2. **Conectar TikTok.** Pulsa «Conectar TikTok»; se abre TikTok Studio. Inicia sesión con
   QR, contraseña o 2FA en esa ventana. La sesión queda en el perfil persistente de
   Chromium (`data/browser/tiktok/`) y sobrevive a reinicios de la aplicación.
3. **Cargar reels.** Introduce la cuenta pública y pulsa «Cargar reels». Se muestran
   miniatura, fecha, duración y descripción de los últimos reels.
4. **Seleccionar y revisar.** Marca los reels que quieras publicar, pulsa «Revisar y
   publicar», edita las descripciones y confirma que tienes derechos sobre el contenido.
5. **Publicar.** Los reels se descargan y se suben a TikTok de uno en uno. La página
   muestra el progreso, los errores y las capturas de pantalla disponibles.

Los reels ya publicados se marcan y no se pueden volver a encolar. Un reintento crea un
trabajo nuevo y conserva el historial.

## Datos y privacidad

Todo queda en el equipo local, dentro de `data/` (ignorada por Git):

| Ruta | Contenido |
|---|---|
| `data/app.db` | Metadatos de reels, cola e historial |
| `data/videos/` | Videos MP4 descargados |
| `data/thumbs/` | Miniaturas |
| `data/sessions/` | Cookies de Instagram |
| `data/browser/` | Perfiles de Chromium con las sesiones |
| `data/logs/screenshots/` | Capturas de errores de automatización |

No compartas la carpeta `data/`: contiene sesiones activas. La aplicación no almacena las
contraseñas de Instagram ni de TikTok y escucha únicamente en la interfaz local
(`127.0.0.1`). No la expongas a Internet.

## Límites y solución de problemas

- **Instagram limita las solicitudes (429).** Espera entre 15 y 60 minutos antes de
  volver a cargar. La aplicación aplica un enfriamiento de 60 segundos entre cargas.
- **Sesión de Instagram caducada.** Vuelve a conectar Instagram; la cola se reanuda
  automáticamente si estaba pausada por autenticación.
- **Sesión de TikTok caducada.** Vuelve a conectar TikTok. Si la cola estaba pausada por
  autenticación, se reanuda sola.
- **Error de selector o cambio en TikTok Studio.** La aplicación captura una pantalla y
  falla el trabajo antes de publicar si no puede confirmar la privacidad pública. Si el
  error ocurre después de pulsar Publicar, el trabajo se marca como incierto: revisa la
  cuenta de TikTok antes de reintentar para no duplicar el video.
- **Cuenta privada o inexistente.** Solo se aceptan cuentas públicas.
- **Chromium no arranca.** Ejecuta de nuevo `setup.bat` para reinstalar los navegadores
  de Playwright.
- **Cola pausada.** Revisa el aviso en la interfaz. Puedes reanudarla con «Reanudar cola»
  cuando corresponda; los errores de autenticación se reanudan al reconectar la cuenta.

## Estructura del proyecto

```
insta2tiktok/
├── app/
│   ├── main.py           # FastAPI, protecciones locales y ciclo de vida
│   ├── config.py         # Rutas y límites
│   ├── db.py             # SQLite (WAL) y esquema
│   ├── schemas.py        # Validación de peticiones
│   ├── routers/          # Página y API JSON
│   ├── services/         # Instagram, TikTok y cola de trabajos
│   ├── templates/        # Interfaz Jinja2
│   └── static/           # CSS, JavaScript y favicon
├── data/                 # Datos locales (no versionar)
├── requirements.txt
├── setup.bat
└── run.bat
```

## Notas técnicas

- Instagram se consulta con `instaloader` usando las cookies de tu propia sesión. No se
  usa ninguna API de pago.
- TikTok se controla con Playwright en una ventana headed; no se usa la API oficial.
- Antes de publicar se verifica que la privacidad del video esté en «Todos»/«Público».
  Si no se puede confirmar, el trabajo falla sin pulsar Publicar.
- Los videos se validan por tamaño y cabecera MP4 antes de subirse.
