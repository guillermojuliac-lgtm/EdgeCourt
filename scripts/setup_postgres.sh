#!/usr/bin/env bash
#
# EdgeCourt - prepara PostgreSQL nativo (rol, bases y configuracion).
#
# Diseno deliberadamente simple: cada sentencia SQL se envia con `psql -c` y
# parametros `-v`. Nada de heredocs canalizados a traves de `sudo`, porque
# cuando sudo pide la contrasena consume ese stdin y el SQL no llega nunca a
# ejecutarse -que es exactamente lo que fallo en la version anterior, en
# silencio y dando la impresion de haber terminado bien-.
#
# La contrasena se genera aqui, NUNCA se imprime y solo acaba en el .env.
#
# Idempotente: se puede ejecutar las veces que haga falta.
#
# Uso:  bash scripts/setup_postgres.sh

set -euo pipefail

# Ningun fallo puede quedar sin explicacion: sin esto, un comando que aborte por
# `set -e` termina el script en silencio y parece que todo ha ido bien.
trap 'printf "\n\033[1;31m[ABORTADO]\033[0m fallo en la linea %s: %s\n" "${LINENO}" "${BASH_COMMAND}" >&2' ERR

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"

DB_ROLE="edgecourt_app"
DB_MAIN="edgecourt"
DB_TEST="edgecourt_test"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
ok()   { printf '    [OK]   %s\n' "$1"; }
fail() { printf '    [ERROR] %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
say "1/7  Autenticacion"
# Se pide sudo UNA vez y por adelantado, para que ninguna llamada posterior se
# quede esperando una contrasena.
sudo -v || fail "se necesita sudo para configurar PostgreSQL"
ok "sudo disponible"

# ---------------------------------------------------------------------------
say "2/7  PostgreSQL instalado"
if ! command -v psql >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y postgresql postgresql-client
fi
ok "$(psql --version)"

# ---------------------------------------------------------------------------
say "3/7  Servicio y puerto"
sudo systemctl enable --now postgresql >/dev/null 2>&1 || true
systemctl is-active --quiet postgresql || fail "el servicio postgresql no esta activo"

# No se asume 5432: puede estar ocupado por otro PostgreSQL de la maquina.
PG_PORT="$(pg_lsclusters -h | awk '$4=="online"{print $3; exit}')"
[[ -n "${PG_PORT}" ]] || fail "no se encontro ningun cluster en linea"
ok "cluster nativo en el puerto ${PG_PORT}"
[[ "${PG_PORT}" == "5432" ]] || printf '    nota: el 5432 lo ocupa otro servicio; EdgeCourt usara el %s\n' "${PG_PORT}"

# Comprobacion temprana: si esto falla, falla todo lo demas.
sudo -u postgres psql -p "${PG_PORT}" -tAc "SELECT 1" >/dev/null \
    || fail "no se puede administrar el cluster del puerto ${PG_PORT}"
ok "acceso administrativo verificado"

# ---------------------------------------------------------------------------
say "4/7  Rol ${DB_ROLE}"
# 24 bytes aleatorios en hexadecimal: 48 caracteres alfanumericos, ~192 bits.
# Sin tuberias a proposito: `... | head -c N` mata al productor con SIGPIPE, y
# con `set -o pipefail` eso aborta el script sin mensaje alguno.
DB_PASSWORD="$(openssl rand -hex 24)"

ROLE_EXISTS="$(sudo -u postgres psql -p "${PG_PORT}" -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname='${DB_ROLE}'")"

if [[ "${ROLE_EXISTS}" == "1" ]]; then
    ok "el rol ya existe"
else
    sudo -u postgres psql -p "${PG_PORT}" -v ON_ERROR_STOP=1 -q \
        -c "CREATE ROLE ${DB_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE"
    ok "rol creado (sin superusuario, sin createdb, sin createrole)"
fi

# La contrasena se fija SIEMPRE, de modo que lo que hay en PostgreSQL y lo que
# se escribe en el .env no puedan quedar desalineados.
#
# Se hace mediante un fichero temporal propiedad de `postgres` y no con `psql -c`
# por dos motivos:
#   1. `psql -c` NO interpola las variables `-v`: la sustitucion `:'pass'` solo
#      ocurre en ficheros (`-f`) o en modo interactivo, asi que el servidor
#      recibiria los dos puntos literales y daria un error de sintaxis;
#   2. interpolar la contrasena directamente en `-c` la dejaria visible en la
#      lista de procesos para cualquier usuario de la maquina.
#
# La contrasena llega al fichero por stdin (via `tee`), nunca como argumento.
sudo -v   # refresca el permiso para que ningun sudo posterior lea del stdin
SQL_FILE="$(sudo -n -u postgres mktemp)"
trap 'sudo -n -u postgres rm -f "${SQL_FILE}" 2>/dev/null || true' EXIT
sudo -n -u postgres chmod 600 "${SQL_FILE}"

# La contrasena es hexadecimal, asi que no puede contener comillas ni escapes.
printf "ALTER ROLE %s PASSWORD '%s';\n" "${DB_ROLE}" "${DB_PASSWORD}" \
    | sudo -n -u postgres tee "${SQL_FILE}" >/dev/null

sudo -n -u postgres psql -p "${PG_PORT}" -v ON_ERROR_STOP=1 -q -f "${SQL_FILE}"
sudo -n -u postgres rm -f "${SQL_FILE}"
ok "contrasena establecida"

# Verificacion real: que la contrasena quedo guardada.
HAS_PASSWORD="$(sudo -u postgres psql -p "${PG_PORT}" -tAc \
    "SELECT rolpassword IS NOT NULL FROM pg_authid WHERE rolname='${DB_ROLE}'")"
[[ "${HAS_PASSWORD}" == "t" ]] || fail "la contrasena no se guardo en el rol"
ok "contrasena verificada en pg_authid"

# ---------------------------------------------------------------------------
say "5/7  Bases de datos"
for db in "${DB_MAIN}" "${DB_TEST}"; do
    EXISTS="$(sudo -u postgres psql -p "${PG_PORT}" -tAc \
        "SELECT 1 FROM pg_database WHERE datname='${db}'")"
    if [[ "${EXISTS}" == "1" ]]; then
        ok "${db} ya existe"
    else
        sudo -u postgres createdb -p "${PG_PORT}" -O "${DB_ROLE}" "${db}"
        ok "${db} creada (propietario ${DB_ROLE})"
    fi

    # Permisos minimos: solo el rol de la aplicacion puede conectarse, y es
    # dueno del esquema public -imprescindible desde PostgreSQL 15 para que las
    # migraciones puedan crear tablas-.
    sudo -u postgres psql -p "${PG_PORT}" -v ON_ERROR_STOP=1 -q -d "${db}" \
        -c "REVOKE CONNECT ON DATABASE ${db} FROM PUBLIC" \
        -c "GRANT CONNECT, TEMPORARY ON DATABASE ${db} TO ${DB_ROLE}" \
        -c "ALTER SCHEMA public OWNER TO ${DB_ROLE}"
    ok "permisos minimos aplicados en ${db}"
done

# Verificacion real: que ambas existen.
FOUND="$(sudo -u postgres psql -p "${PG_PORT}" -tAc \
    "SELECT count(*) FROM pg_database WHERE datname IN ('${DB_MAIN}','${DB_TEST}')")"
[[ "${FOUND}" == "2" ]] || fail "esperaba 2 bases de datos, encontre ${FOUND}"
ok "ambas bases verificadas"

# ---------------------------------------------------------------------------
say "6/7  Configuracion en .env"
MAIN_DSN="postgresql://${DB_ROLE}:${DB_PASSWORD}@127.0.0.1:${PG_PORT}/${DB_MAIN}"
TEST_DSN="postgresql://${DB_ROLE}:${DB_PASSWORD}@127.0.0.1:${PG_PORT}/${DB_TEST}"

umask 077
[[ -f "${ENV_FILE}" ]] || : > "${ENV_FILE}"

# Se eliminan todas las apariciones previas y se anade una sola linea nueva.
TMP_ENV="$(mktemp "${ENV_FILE}.XXXXXX")"
chmod 600 "${TMP_ENV}"
grep -vE '^(DATABASE_URL|EDGECOURT_TEST_DSN)=' "${ENV_FILE}" > "${TMP_ENV}" || true
printf 'DATABASE_URL=%s\n' "${MAIN_DSN}" >> "${TMP_ENV}"
printf 'EDGECOURT_TEST_DSN=%s\n' "${TEST_DSN}" >> "${TMP_ENV}"
mv "${TMP_ENV}" "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

# Verificacion real: que ambas quedaron escritas y con valor.
for key in DATABASE_URL EDGECOURT_TEST_DSN; do
    line="$(grep -E "^${key}=" "${ENV_FILE}" || true)"
    [[ -n "${line}" ]]        || fail "${key} no se escribio en .env"
    [[ "${line}" != "${key}=" ]] || fail "${key} quedo vacia en .env"
done
ok "DATABASE_URL y EDGECOURT_TEST_DSN escritas (.env con permisos 600)"

# ---------------------------------------------------------------------------
say "7/7  Conexion como ${DB_ROLE}"
for db in "${DB_MAIN}" "${DB_TEST}"; do
    dsn="postgresql://${DB_ROLE}:${DB_PASSWORD}@127.0.0.1:${PG_PORT}/${db}"
    result="$(psql "${dsn}" -tAc "SELECT current_user || '@' || current_database()")" \
        || fail "no se pudo conectar a ${db}"
    ok "conexion correcta: ${result}"
done

unset DB_PASSWORD MAIN_DSN TEST_DSN dsn

printf '\n\033[1mListo.\033[0m La contrasena no se ha mostrado en ningun momento.\n'
printf 'Siguiente: uv run edgecourt db migrate --dry-run\n\n'
