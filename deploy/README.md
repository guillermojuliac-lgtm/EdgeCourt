# Despliegue

Ficheros de ejemplo para ejecutar EdgeCourt como servicio en Ubuntu.

> **Nada de esto se instala automaticamente.** Revisa cada fichero, ajusta rutas
> y usuario, y copialo tu mismo. El brief (§20) lo exige explicitamente: hay que
> ver que se va a instalar antes de instalarlo.

## Unidades disponibles

| Fichero | Que hace | Fase |
|---|---|---|
| `edgecourt-collector.service` | Recoge cuotas de Betfair en solo lectura, 24/7 | 8 ✅ |
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
