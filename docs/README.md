# Documentación de EdgeCourt

Índice de la documentación y convención de memoria persistente del proyecto.
**Punto de entrada:** [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Mapa

| Documento | Para qué sirve | Naturaleza |
|---|---|---|
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | Estado **actual** real del proyecto | Vivo, se reescribe |
| [`ROADMAP.md`](ROADMAP.md) | Fases: completado, en curso, pendiente y bloqueado | Vivo |
| [`DECISIONS.md`](DECISIONS.md) | Decisiones con su porqué (ADR simplificado) | Solo se añade; se sustituye, no se reescribe |
| [`CHANGELOG_TECHNICAL.md`](CHANGELOG_TECHNICAL.md) | Hitos técnicos relevantes, en lenguaje humano | Solo se añade |
| [`phases/`](phases/) | Un documento por fase nueva o en curso | Vivo mientras la fase está abierta |
| [`audits/`](audits/) | Auditorías con fecha | **Inmutables** |
| `investigations/` | Investigaciones con fecha (se crea con la primera) | Inmutables una vez cerradas |
| [`architecture/`](architecture/) | Componentes en operación: PostgreSQL, collector | Vivo |
| [`DATA.md`](DATA.md) | Fuentes, esquema `match_facts`, split, resultados de PHASE 1 | Referencia |
| [`MODELS.md`](MODELS.md) | Features, Elo, LogReg: decisiones y resultados | Referencia |
| [`METRICS.md`](METRICS.md) | Definiciones y jerarquía de métricas, fijadas de antemano | Referencia |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Principios, separación de responsabilidades, módulos | Referencia |
| [`BETFAIR_SETUP.md`](BETFAIR_SETUP.md) | Credenciales, certificado y verificación | Guía |
| [`../IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) | Plan original: 16 fases, riesgos R1–R13, decisiones D1–D12 | Referencia histórica |
| [`../deploy/README.md`](../deploy/README.md) | Unidades systemd | Guía |

Las fases 0–3 y 8 (numeración del plan) ya están documentadas en `IMPLEMENTATION_PLAN.md`,
`DATA.md` y `MODELS.md`. **No se han duplicado** en `phases/`.

---

## Convención de trabajo

### Antes de una fase o de una tarea importante
1. Leer [`PROJECT_STATUS.md`](PROJECT_STATUS.md).
2. Leer el documento de la fase (`phases/…`, o su sección en el plan).
3. Revisar las decisiones relevantes en [`DECISIONS.md`](DECISIONS.md).

### Durante
- Implementar y testear.
- **No cambiar decisiones arquitectónicas en silencio.** Si una decisión deja de valer, se
  registra una nueva que la sustituya.
- No mezclar tareas: la documentación de una fase habla de esa fase.

### Después
1. Actualizar el documento de la fase: qué se hizo, resultados y cifras.
2. Guardar los resultados experimentales: en el documento, o en un fichero enlazado si son
   extensos.
3. Registrar las decisiones nuevas en `DECISIONS.md`.
4. Actualizar `PROJECT_STATUS.md`, que refleja el estado **actual** y no es un histórico.
5. Añadir una entrada a `CHANGELOG_TECHNICAL.md`: fecha, cambio, motivo, resultado, tests y
   commit.
6. Indicar los tests ejecutados (comando y resultado).
7. Indicar el commit cuando exista.

### Auditorías
- Cada auditoría significativa va en `docs/audits/YYYY-MM-<nombre>.md`.
- Deben incluir: fecha, fuente y corte de los datos, modo (solo lectura), definiciones, métricas
  y conclusiones.
- Los scripts y la salida completa se guardan en un directorio hermano con el mismo nombre.
- **No se sobrescriben nunca.** Son snapshots del conocimiento disponible en ese momento. Si hay
  datos nuevos, se hace una auditoría nueva que enlace a la anterior y compare.

### Investigaciones
- Van en `docs/investigations/YYYY-MM-<tema>.md`.
- Estructura: pregunta, hipótesis, evidencia por hipótesis, conclusión y decisiones derivadas.
- No se asume una causa sin evidencia; lo que no se pudo verificar se dice.

### Seguridad en la documentación
- Nunca incluir: valores del `.env`, contraseñas, App Key, token de sesión, claves privadas o
  certificados, ni DSN con contraseña.
- Las rutas a secretos se describen de forma genérica.
- Antes de cerrar una tarea de documentación, buscar patrones de secretos en `docs/`.
