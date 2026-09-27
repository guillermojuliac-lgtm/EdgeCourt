#!/usr/bin/env bash
#
# EdgeCourt - instala el timer de la auditoria de catalogo (Phase 3.5-C2).
#
# Muestra las dos unidades antes de instalarlas y pide confirmacion (brief §20:
# hay que ver que se va a instalar antes de instalarlo). No toca el collector.
#
# Uso:  bash scripts/install_catalogue_audit.sh

set -euo pipefail
trap 'printf "\n\033[1;31m[ABORTADO]\033[0m fallo en la linea %s: %s\n" "${LINENO}" "${BASH_COMMAND}" >&2' ERR

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="edgecourt-catalogue-audit.service"
TIMER="edgecourt-catalogue-audit.timer"
DATA_DIR="${PROJECT_DIR}/data/research/catalogue_audit"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
ok()   { printf '    [OK]   %s\n' "$1"; }
fail() { printf '    [ERROR] %s\n' "$1" >&2; exit 1; }

say "1/6  Comprobaciones previas"

for unit in "${SERVICE}" "${TIMER}"; do
    [[ -f "${PROJECT_DIR}/deploy/${unit}" ]] || fail "no se encuentra deploy/${unit}"
done
ok "unidades presentes"

BIN="${PROJECT_DIR}/.venv/bin/edgecourt"
[[ -x "${BIN}" ]] || fail "no existe el ejecutable ${BIN}. Ejecuta antes: uv sync"
ok "ejecutable presente"

[[ -f "${PROJECT_DIR}/.env" ]] || fail "no existe ${PROJECT_DIR}/.env"
ok ".env presente"

MODE="$("${BIN}" status 2>/dev/null | awk -F': *' '/Modo de apuesta/{print $2}')"
[[ "${MODE}" == "PAPER" ]] || fail "BETTING_MODE no es paper (leido: '${MODE}')"
ok "modo de apuesta: PAPER"

# ReadWritePaths exige que el directorio exista. Se crea como el usuario actual,
# sin sudo, con permisos 700.
mkdir -p -m 700 "${DATA_DIR}"
ok "directorio de datos: ${DATA_DIR}"

say "2/6  Unidades que se van a instalar"
for unit in "${SERVICE}" "${TIMER}"; do
    printf '\n    %s -> /etc/systemd/system/%s\n\n' "deploy/${unit}" "${unit}"
    sed 's/^/    | /' "${PROJECT_DIR}/deploy/${unit}"
done

printf '\n'
read -r -p "Instalar estas dos unidades y activar el timer? [s/N] " RESPUESTA
[[ "${RESPUESTA}" =~ ^[sSyY]$ ]] || { printf '    Cancelado.\n'; exit 0; }

say "3/6  Copiando e instalando"
for unit in "${SERVICE}" "${TIMER}"; do
    sudo install -m 644 -o root -g root "${PROJECT_DIR}/deploy/${unit}" "/etc/systemd/system/${unit}"
done
sudo systemctl daemon-reload
ok "unidades instaladas y daemon recargado"

say "4/6  Validando las unidades"
sudo systemd-analyze verify "/etc/systemd/system/${SERVICE}" "/etc/systemd/system/${TIMER}" 2>&1 \
    | sed 's/^/    /' || true
ok "validacion ejecutada"

say "5/6  Activando el timer (NO se ejecuta la auditoria ahora)"
sudo systemctl enable --now "${TIMER}"
ok "timer activo"

say "6/6  Proximas ejecuciones"
systemctl list-timers "${TIMER}" --no-pager | sed 's/^/    /'

printf '\n\033[1mListo.\033[0m\n'
printf '  Ejecuciones : journalctl -u %s\n' "${SERVICE}"
printf '  Datos       : %s\n' "${DATA_DIR}"
printf '  Informe     : %s/.venv/bin/edgecourt betfair catalogue-report --out <dir>\n' "${PROJECT_DIR}"
printf '  Desactivar  : sudo systemctl disable --now %s\n\n' "${TIMER}"
