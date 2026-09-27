# Despliegue

Ficheros de ejemplo para ejecutar EdgeCourt como servicio en Ubuntu.

> **Nada de esto se instala automaticamente.** Revisa cada fichero, ajusta rutas
> y usuario, y copialo tu mismo. El brief (§20) lo exige explicitamente: hay que
> ver que se va a instalar antes de instalarlo.

## Unidades disponibles

| Fichero | Que hace | Fase |
|---|---|---|
| `edgecourt-collector.service` | Recoge cuotas de Betfair en solo lectura, 24/7 | 8 ✅ |
| `edgecourt-catalogue-audit.service` + `.timer` | Snapshot de solo lectura del catálogo de tenis cada 30 min, sin PostgreSQL (experimento del 2026-09-29 al 2026-10-19) | 3.5-C2 |
| `edgecourt-predictor.service` | Genera predicciones y evalua value | 9 |
| `edgecourt-training.service` + `.timer` | Reentrenamiento semanal del challenger | 14 |

## Preparacion

```bash
# Usuario sin privilegios, sin shell de login
sudo useradd --system --shell /usr/sbin/nologin --home /opt/edgecourt edgecourt

sudo mkdir -p /opt/edgecourt /etc/edgecourt
sudo chown -R edgecourt:edgecourt /opt/edgecourt

# Fichero de entorno con las credenciales, solo legible por su duenno
sudo install -o edgecourt -g edgecourt -m 600 /dev/null /etc/edgecourt/edgecourt.env
sudo -u edgecourt editor /etc/edgecourt/edgecourt.env   # copiar de .env.example
```

Los certificados de Betfair van **fuera del repositorio**, por ejemplo en
`/etc/edgecourt/betfair/`, con permisos `600` y propiedad del usuario del servicio.

## Comprobacion antes de habilitar

```bash
sudo -u edgecourt /opt/edgecourt/.venv/bin/edgecourt status
sudo -u edgecourt /opt/edgecourt/.venv/bin/edgecourt collector start --max-cycles 1
```

El segundo comando hace un unico ciclo y termina. Si las credenciales faltan o
son incorrectas, sale con codigo 5 y dice exactamente que variable falta.

## Auditoria de catalogo (Phase 3.5-C2)

`edgecourt-catalogue-audit.service` (oneshot) y `edgecourt-catalogue-audit.timer` (cada 30 min,
`RandomizedDelaySec=120`, `Persistent=false`) son independientes del collector:

- otra unidad, otro proceso y otra sesion de Betfair;
- **sin dependencia ni acceso a PostgreSQL**;
- escritura solo en `data/research/`, donde tambien se redirigen los logs (`LOGS_DIR`);
- mismo endurecimiento que el collector, con `UMask=0077`.

Fuera de la ventana 2026-09-29 00:00 UTC → 2026-10-19 00:00 UTC, el propio comando termina sin
llamar a Betfair, aunque el timer siga activo.

```bash
bash scripts/install_catalogue_audit.sh             # muestra las unidades y pide confirmacion
journalctl -u edgecourt-catalogue-audit             # ejecuciones
sudo systemctl disable --now edgecourt-catalogue-audit.timer   # al terminar el experimento
```

Protocolo del experimento: `docs/investigations/2026-10-spanish-exchange-atp-catalogue.md`.
