#!/usr/bin/env bash
#
# EdgeCourt - instala el servicio systemd del collector.
#
# Muestra la unidad antes de instalarla (brief §20: hay que ver que se va a
# instalar antes de instalarlo) y verifica los requisitos previos.
#
# Uso:  bash scripts/install_service.sh

set -euo pipefail
trap 'printf "\n\033[1;31m[ABORTADO]\033[0m fallo en la linea %s: %s\n" "${LINENO}" "${BASH_COMMAND}" >&2' ERR

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_NAME="edgecourt-collector.service"
UNIT_SRC="${PROJECT_DIR}/deploy/${UNIT_NAME}"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
ok()   { printf '    [OK]   %s\n' "$1"; }
fail() { printf '    [ERROR] %s\n' "$1" >&2; exit 1; }

say "1/6  Comprobaciones previas"

[[ -f "${UNIT_SRC}" ]] || fail "no se encuentra ${UNIT_SRC}"

BIN="${PROJECT_DIR}/.venv/bin/edgecourt"
[[ -x "${BIN}" ]] || fail "no existe el ejecutable ${BIN}. Ejecuta antes: uv sync"
ok "ejecutable presente"

[[ -f "${PROJECT_DIR}/.env" ]] || fail "no existe ${PROJECT_DIR}/.env"
PERMS="$(stat -c '%a' "${PROJECT_DIR}/.env")"
[[ "${PERMS}" == "600" ]] || printf '    AVISO: .env tiene permisos %s, se recomienda 600\n' "${PERMS}"
ok ".env presente"

# El servicio no puede arrancar si el modo no es paper.
MODE="$("${BIN}" status 2>/dev/null | awk -F': *' '/Modo de apuesta/{print $2}')"
[[ "${MODE}" == "PAPER" ]] || fail "BETTING_MODE no es paper (leido: '${MODE}')"
ok "modo de apuesta: PAPER"

say "2/6  Unidad que se va a instalar"
printf '    %s -> %s\n\n' "${UNIT_SRC}" "${UNIT_DST}"
sed 's/^/    | /' "${UNIT_SRC}"

printf '\n'
read -r -p "Instalar esta unidad? [s/N] " RESPUESTA
[[ "${RESPUESTA}" =~ ^[sSyY]$ ]] || { printf '    Cancelado.\n'; exit 0; }

say "3/6  Copiando e instalando"
sudo install -m 644 -o root -g root "${UNIT_SRC}" "${UNIT_DST}"
sudo systemctl daemon-reload
ok "unidad instalada y daemon recargado"

say "4/6  Validando la unidad"
sudo systemd-analyze verify "${UNIT_DST}" 2>&1 | sed 's/^/    /' || true
ok "validacion ejecutada"

say "5/6  Habilitando el arranque automatico"
sudo systemctl enable "${UNIT_NAME}"
ok "se iniciara con el sistema"

say "6/6  Arrancando"
sudo systemctl start "${UNIT_NAME}"
sleep 3
sudo systemctl status "${UNIT_NAME}" --no-pager --lines=0 | sed 's/^/    /'

printf '\n\033[1mListo.\033[0m\n'
printf '  Estado : systemctl status %s\n' "${UNIT_NAME}"
printf '  Logs   : journalctl -u %s -f\n' "${UNIT_NAME}"
printf '  Salud  : cd %s && uv run edgecourt collector health\n\n' "${PROJECT_DIR}"
