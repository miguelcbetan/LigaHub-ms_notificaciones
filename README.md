# emails_google

Microservicio Flask de LigaHub que entrega por Gmail el correo del segundo factor (2FA). **No autentica usuarios**: el código lo genera, guarda (solo su HMAC), expira y valida `ms-security`; este servicio únicamente envía el mensaje.

## Contrato

`POST /api/2fa/send`

| Elemento | Valor |
|---|---|
| Cabecera obligatoria | `X-Service-Key: <EMAIL_SERVICE_API_KEY>` |
| Cuerpo (JSON, solo estos 3 campos) | `{"email": "...", "code": "123456", "expires_minutes": 5}` |
| `email` | texto, máx. 254 caracteres, sin espacios ni saltos de línea |
| `code` | exactamente 6 dígitos (texto) |
| `expires_minutes` | entero entre 1 y 30 |

Respuestas: `200` enviado · `400` datos inválidos · `401` clave de servicio ausente o incorrecta · `429` límite de envíos · `500` servicio sin configurar o Gmail sin autorizar · `502` Gmail no pudo enviar. Los errores nunca incluyen detalles del proveedor.

Seguridad: clave compartida comparada en tiempo constante, límite de envíos por IP y por destinatario (en memoria, una sola instancia), escucha solo en `127.0.0.1` por defecto, y los logs no contienen el código, la clave ni el correo completo (se enmascara como `ab***@dominio`).

## Estructura

```
app.py            rutas Flask y arranque (create_app)
config.py         variables de entorno -> Settings
mailer.py         construcción del mensaje (texto + HTML) y envío por Gmail API
ratelimit.py      límite de envíos en ventana deslizante
templates/        two_factor.html / two_factor.txt (presentación, separada de la lógica)
assets/logo.png   logo embebido en el correo (CID)
tests/            pruebas con pytest (Gmail simulado)
```

## Configuración

Los valores se escriben **una sola vez** en `emails_google/.env` (archivo local, en `.gitignore`; la plantilla es `.env.example`). El servicio lo carga solo al arrancar: no hay que exportar nada en la consola. Si una variable también está definida en el sistema, esa tiene prioridad sobre el archivo, y un valor vacío cuenta como «no configurado».

| Variable | Descripción |
|---|---|
| `EMAIL_SERVICE_API_KEY` | Clave compartida con `ms-security` (`email-service.api-key` en su `application.properties`): **debe ser idéntica en ambos archivos**. Obligatoria. |
| `GMAIL_SENDER` | Cuenta de Gmail autorizada para enviar. Obligatoria (sin valor por defecto): **pégala en `.env`** (línea `>>> PEGA AQUI`). |
| `EMAIL_SERVICE_HOST` / `EMAIL_SERVICE_PORT` | Por defecto `127.0.0.1` / `5000`. |
| `GMAIL_CREDENTIALS_DIR` | Carpeta con `credentials.json` y `token.pickle` (por defecto `./credentials`). |
| `EMAIL_RATE_WINDOW_SECONDS`, `EMAIL_RATE_PER_RECIPIENT`, `EMAIL_RATE_PER_IP` | Límites de envío. |

### Autorizar Gmail (una sola vez)

1. En Google Cloud crea un cliente OAuth de tipo *Aplicación de escritorio* con la Gmail API habilitada y descarga su `credentials.json` a la carpeta de credenciales.
2. Ejecuta `python app.py --authorize`: abre el navegador y guarda `token.pickle`. El servicio nunca abre el navegador por sí mismo mientras atiende peticiones.

`token.pickle` se carga con `pickle`: debe generarlo este servicio, nunca provenir de terceros. **No dejes `credentials/` en carpetas sincronizadas (OneDrive, Dropbox…)**: contiene un secreto de cliente y un refresh token que permite enviar correo como la cuenta remitente.

## Ejecución

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
# edita antes emails_google\.env (EMAIL_SERVICE_API_KEY y GMAIL_SENDER); no hay que exportar variables
.venv\Scripts\python app.py
```

El servidor integrado de Flask es suficiente para uso local. Para producción, ejecútalo detrás de un servidor WSGI y no lo expongas fuera de la red interna.

## Pruebas

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

No necesitan credenciales reales: Gmail se sustituye por dobles de prueba.
