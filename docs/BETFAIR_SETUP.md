# Configurar Betfair en EdgeCourt

Guía completa para dejar el collector de cuotas funcionando. Unos 20 minutos, más el
tiempo que Betfair tarde en verificar la cuenta.

> **EdgeCourt solo lee.** No existe ninguna función capaz de enviar, modificar o cancelar una
> apuesta. Nada de lo que configures aquí habilita apostar: ver [Garantía de solo lectura](#garantía-de-solo-lectura).

---

## Regla de oro sobre los secretos

**Ninguna credencial se escribe nunca en el código, ni se pega en un chat, ni se sube a un
issue, ni se comparte en una captura.** Todo va a un fichero `.env` local que `.gitignore` ya
excluye, y los certificados viven **fuera del repositorio**.

Si en algún momento una credencial se expone: cámbiala en Betfair inmediatamente y regenera el
certificado. Un secreto que ha pasado por un chat o un repositorio debe considerarse quemado,
aunque lo borres después.

---

## Resumen

| Paso | Qué obtienes | Coste | Tiempo |
|---|---|---|---|
| 1. Cuenta verificada | Acceso a la API | — | variable (KYC) |
| 2. Application Key | `BETFAIR_APP_KEY` | gratis (delayed) / £499 (live) | minutos |
| 3. Certificado | `.crt` y `.key` | gratis | 2 minutos |
| 4. Configurar `.env` | Collector operativo | — | 2 minutos |

---

## Paso 1 — Cuenta verificada

Necesitas una cuenta de Betfair verificada según su política KYC. Sin verificar, la creación de
la Application Key falla.

## Paso 2 — Application Key

La *Application Key* identifica a tu cliente y debe ir en **todas** las peticiones.

1. Inicia sesión en Betfair en una pestaña del navegador.
2. Abre el [portal de desarrolladores](https://developer.betfair.com/) y entra en la
   **Accounts API Demo Tool**.
3. Selecciona la operación **`createDeveloperAppKeys`**.
4. Refresca la página para que se rellene automáticamente tu token de sesión.
5. Introduce un **nombre de aplicación único a nivel global** — si ya existe, falla. Algo como
   `edgecourt-<algo-tuyo>` funciona bien.
6. Pulsa **Execute**.

Se generan **dos** claves de golpe:

| Clave | Estado inicial | Datos que devuelve | Coste |
|---|---|---|---|
| **Delayed** | activa | retrasados | **gratis** |
| **Live** | inactiva | tiempo real | **tasa única de activación de £499** |

### Cuál usar

**Empieza con la Delayed Key.** Para los hitos lejanos del collector (24 h, 12 h, 6 h) el
retraso es irrelevante: el precio no se mueve de forma apreciable en unos segundos cuando
faltan horas para el partido. Te permite validar el pipeline completo y empezar a acumular
muestra sin gastar nada.

La Live Key solo se justifica cuando la investigación demuestre que hace falta precisión en los
hitos cercanos (10 min y cierre).

> **Salvedad importante sobre el CLV.** El Closing Line Value se mide contra el precio de
> cierre, que es justamente el que más se mueve. Con datos retrasados, el CLV es **orientativo,
> no concluyente**. Dado que el CLV es una de nuestras dos métricas de nivel 1
> ([docs/METRICS.md](METRICS.md)), esta es la razón real por la que en algún momento podría
> hacer falta la Live Key — no la comodidad.

Verifica importe y condiciones en el portal antes de decidir: pueden haber cambiado.

## Paso 3 — Certificado autofirmado

Imprescindible para operar 24/7. Sin certificado solo existe el login interactivo, cuya sesión
caduca y no puede renovarse sin intervención humana — lo que rompe la premisa de un proceso
desatendido.

Betfair exige **RSA de 2048 bits**.

```bash
mkdir -p ~/.config/edgecourt/betfair
cd ~/.config/edgecourt/betfair

# 1. Clave privada
openssl genrsa -out client-2048.key 2048

# 2. Petición de firma
openssl req -new -key client-2048.key -out client-2048.csr

# 3. Certificado autofirmado (10 años)
openssl x509 -req -days 3650 -in client-2048.csr -signkey client-2048.key -out client-2048.crt

# 4. Permisos restrictivos
chmod 600 client-2048.key client-2048.crt
```

Sobre el paso 2: puedes dejar todos los campos en blanco salvo **Common Name**, donde conviene
poner algo identificable.

> **No pongas contraseña a la clave privada.** Un proceso desatendido no puede teclearla al
> arrancar. La protección aquí son los permisos del fichero y que esté fuera del repositorio.

### Subirlo a tu cuenta

1. Ve a `https://myaccount.betfair.com/accountdetails/mysecurity?showAPI=1`
2. Busca la sección **"Automated Betting Program Access"** y pulsa **Edit**.
3. Pulsa **Browse** y selecciona **`client-2048.crt`**.
4. Pulsa **Upload Certificate**.

> Sube el **`.crt`**, no el `.csr`. Es el error más común.

**Otras jurisdicciones**, misma ruta cambiando el dominio:

| País | URL |
|---|---|
| España | `https://myaccount.betfair.es/accountdetails/mysecurity?showAPI=1` |
| Italia | `https://myaccount.betfair.it/accountdetails/mysecurity?showAPI=1` |
| Australia | `https://myaccount.betfair.com.au/accountdetails/mysecurity?showAPI=1` |

## Paso 4 — Configurar EdgeCourt

```bash
cp .env.example .env
```

Edita `.env` y rellena:

```ini
BETFAIR_USERNAME=tu_usuario
BETFAIR_PASSWORD=tu_contrasena
BETFAIR_APP_KEY=tu_application_key

# Rutas ABSOLUTAS, fuera del repositorio
BETFAIR_CERT_PATH=/home/TU_USUARIO/.config/edgecourt/betfair/client-2048.crt
BETFAIR_KEY_PATH=/home/TU_USUARIO/.config/edgecourt/betfair/client-2048.key
```

Comprueba que `.env` no acabe en git:

```bash
git check-ignore -v .env     # debe responder que está ignorado
```

## Paso 5 — Verificar

**Antes de arrancar el collector**, comprueba credenciales y acceso de lectura de forma
aislada:

```bash
uv run edgecourt betfair check
```

Este comando **no escribe absolutamente nada en disco** y cierra la sesión al terminar.
Comprueba, parándose en el primer fallo:

1. que la configuración está completa;
2. que la clave privada tiene permisos `600` y no está cifrada con passphrase;
3. que el par clave/certificado coincide, es RSA de 2048 bits y sigue vigente;
4. que el login por certificado funciona;
5. que se pueden leer eventos, mercados y precios.

Salida esperada:

```
  [ OK  ] Configuracion          usuario definido, app key de NN caracteres
  [ OK  ] Permisos de la clave   solo accesible por su propietario
  [ OK  ] Clave sin passphrase   apta para ejecucion desatendida
  [ OK  ] Par clave/certificado  coinciden
  [ OK  ] Tamano de clave        RSA de 2048 bits
  [ OK  ] Vigencia               valido hasta 2036-09-15 (3650 dias)
  [ OK  ] Login por certificado  sesion obtenida
  [ OK  ] Lectura de eventos     NN eventos de tenis visibles
  [ OK  ] Lectura de mercados    N mercados Match Odds
  [ OK  ] Lectura de precios     N libros de precios
```

| Código de salida | Significado |
|---|---|
| `0` | Todo correcto |
| `5` | Alguna comprobación falló — el detalle dice cuál y por qué |

Ningún secreto aparece en la salida: de la app key solo se muestra su longitud, y del token de
sesión nada en absoluto.

Cuando pase, ya puedes recolectar:

```bash
uv run edgecourt collector start --max-cycles 1   # un ciclo y termina (SI escribe snapshots)
uv run edgecourt collector start                  # hasta recibir SIGTERM
uv run edgecourt collector status                 # cobertura por hito
```

### Cuidado al escribir el `.env`

El fichero se lee línea a línea, sin shell, así que:

- **No pongas comillas** salvo que formen parte del valor.
- Si la contraseña contiene `#`, espacios o caracteres raros, **entrecomíllala**:
  `BETFAIR_PASSWORD='mi#contrasena rara'`.
- Las rutas deben ser **absolutas**: `~` no se expande.

---

## Resolución de problemas

| Síntoma | Causa | Solución |
|---|---|---|
| **HTTP 403** en el login | Endpoint incorrecto: Betfair bloquea cualquier otro | EdgeCourt usa `https://identitysso-cert.betfair.com/api/certlogin`, que es el correcto. Un 403 apunta a un proxy o cortafuegos que reescribe la petición |
| `CERT_AUTH_REQUIRED` | El certificado no llegó en la conexión TLS | Revisa que `BETFAIR_CERT_PATH` y `BETFAIR_KEY_PATH` apunten a los ficheros correctos y que el `.crt` esté subido a tu cuenta |
| `INVALID_USERNAME_OR_PASSWORD` | Credenciales incorrectas | Ojo: es el usuario de Betfair, no el email, en algunas jurisdicciones |
| `ACCOUNT_NOW_LOCKED` | Demasiados intentos fallidos | Desbloquea desde la web de Betfair |
| `ACCOUNT_PENDING_PASSWORD_CHANGE` | Betfair exige cambiar la contraseña | Cámbiala en la web y actualiza el `.env` |
| `INVALID_APP_KEY` | App key mal copiada o no activa | Verifica en el portal; recuerda que la live nace inactiva |
| `TOO_MANY_REQUESTS` | Límite de peso de la API | EdgeCourt ya reintenta con espera exponencial. Si es persistente, sube `COLLECTOR_INTERVAL_SECONDS` |
| `FileNotFoundError` al arrancar | Rutas de certificado mal puestas | Deben ser **absolutas**; `~` no se expande en un `.env` |
| Sale con código 5 | Falta una variable | El mensaje la nombra explícitamente |

Para diagnosticar, el log del collector está en `logs/collector.log` en formato JSON. **Los
secretos se redactan automáticamente**, tanto por valor conocido como por patrón, así que puedes
compartir ese fichero sin exponer credenciales — aunque conviene revisarlo igualmente antes.

---

## Despliegue como servicio

Ver [`deploy/README.md`](../deploy/README.md). Dos cuestiones de seguridad propias del servicio:

- Las credenciales van en `/etc/edgecourt/edgecourt.env` con permisos `600` y propiedad del
  usuario del servicio, **nunca** dentro del fichero `.service`, que es legible por cualquiera.
- Los certificados, igual: fuera del repositorio, permisos `600`.

La unidad de ejemplo limita los reinicios (`StartLimitBurst=5`), de modo que un fallo de
credenciales no entre en un bucle infinito de reintentos sin que nadie se entere.

---

## Garantía de solo lectura

EdgeCourt implementa exactamente tres operaciones, todas de consulta: `listEvents`,
`listMarketCatalogue` y `listMarketBook`. Cuatro tests automáticos lo verifican en cada
ejecución de la suite:

1. `test_source_contains_no_order_placement_calls` — escanea el árbol en busca de endpoints de
   ejecución de órdenes.
2. `test_no_trading_library_is_imported` — prohíbe importar librerías con capacidad de ejecución.
3. `test_trading_libraries_are_not_installed` — comprueba que ni siquiera están en el entorno.
4. `test_market_package_exposes_no_write_operations` — el cliente solo puede exponer las tres
   operaciones de lectura.

Por eso EdgeCourt usa `httpx` directo y **no** `betfairlightweight`: esa librería agrupa la
colocación de órdenes en el mismo objeto cliente que las consultas, lo que dejaría la capacidad
de mover dinero real a un `import` de distancia dentro del proceso. Escribiendo a mano las tres
llamadas que necesitamos, ese código sencillamente no existe.

Además, `BETTING_MODE` solo admite el valor `paper`: cualquier otro impide arrancar la
aplicación, porque está declarado como un tipo cerrado y no como una comprobación.

---

## Referencias

- [Portal de desarrolladores](https://developer.betfair.com/)
- [Login no interactivo](https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687915/Non-Interactive+bot+login)
- [Subir el certificado](https://support.developer.betfair.com/hc/en-us/articles/20285563815196-How-do-I-upload-my-self-signed-certificate-to-my-Betfair-Account)
- [Cómo empezar](https://support.developer.betfair.com/hc/en-us/articles/115003864651-How-do-I-get-started)
- [Error 403 en el login no interactivo](https://support.developer.betfair.com/hc/en-us/articles/20463610578588-Why-Is-the-Non-Interactive-Endpoint-Login-Returning-a-403-Forbidden-Error)

*Verificado el 2026-09-18. Betfair cambia su portal y sus tarifas de vez en cuando: si algo no
coincide, manda la documentación oficial.*
