#!/usr/bin/env bash
#
# EdgeCourt - diagnostico de la configuracion de PostgreSQL.
#
# Solo lectura. NO imprime contrasenas ni valores de configuracion: unicamente
# nombres de variables, si tienen valor, y el estado del servidor.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
DB_ROLE="edgecourt_app"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

say "1/5  Variables en .env (solo nombres, nunca valores)"
if [[ -f "${ENV_FILE}" ]]; then
    printf '    permisos: %s\n' "$(stat -c '%a' "${ENV_FILE}")"
    for key in DATABASE_URL EDGECOURT_TEST_DSN; do
        count="$(grep -cE "^${key}=" "${ENV_FILE}" 2>/dev/null)" || count=0
        if [[ "${count}" -eq 0 ]]; then
            printf '    %-22s AUSENTE\n' "${key}"
        elif [[ "${count}" -gt 1 ]]; then
            printf '    %-22s DUPLICADA (%s veces) <-- problema\n' "${key}" "${count}"
        else
            value="$(grep -E "^${key}=" "${ENV_FILE}" | head -1 | cut -d= -f2-)"
            if [[ -z "${value}" ]]; then
                printf '    %-22s presente pero VACIA\n' "${key}"
            else
                # Solo se muestra la estructura, nunca la contrasena.
                printf '    %-22s definida  (%s)\n' "${key}" \
                    "$(sed -E 's#(://[^:]*:)[^@]*(@)#\1***\2#' <<<"${value}")"
            fi
        fi
    done
else
    printf '    .env NO EXISTE\n'
fi

say "2/5  Cluster de PostgreSQL"
pg_lsclusters 2>/dev/null || printf '    pg_lsclusters no disponible\n'

PG_PORT="$(pg_lsclusters -h 2>/dev/null | awk '$4=="online"{print $3; exit}')"
printf '    puerto del cluster nativo: %s\n' "${PG_PORT:-desconocido}"

say "3/5  Rol ${DB_ROLE}"
sudo -u postgres psql -p "${PG_PORT}" -tA -c \
    "SELECT rolname || ' | login=' || rolcanlogin || ' | super=' || rolsuper ||
            ' | createdb=' || rolcreatedb || ' | password_set=' ||
            (rolpassword IS NOT NULL)
     FROM pg_roles r
     LEFT JOIN pg_authid a USING (oid)
     WHERE rolname = '${DB_ROLE}'" 2>/dev/null \
  || printf '    no se pudo consultar (se requiere sudo)\n'

say "4/5  Bases de datos de EdgeCourt"
sudo -u postgres psql -p "${PG_PORT}" -tA -c \
    "SELECT datname || ' | owner=' || pg_get_userbyid(datdba)
     FROM pg_database WHERE datname LIKE 'edgecourt%' ORDER BY 1" 2>/dev/null \
  || printf '    no se pudo consultar\n'

say "5/5  Metodo de autenticacion para 127.0.0.1"
sudo grep -vE '^\s*#|^\s*$' "/etc/postgresql/$(pg_lsclusters -h | awk '{print $1; exit}')/main/pg_hba.conf" 2>/dev/null \
    | grep -E '^(host|local)' \
  || printf '    no se pudo leer pg_hba.conf\n'

printf '\n\033[1mDiagnostico terminado.\033[0m Ninguna contrasena se ha mostrado.\n\n'
