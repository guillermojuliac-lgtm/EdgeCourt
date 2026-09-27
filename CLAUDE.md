# EdgeCourt — instrucciones para sesiones de Claude

Este fichero es un **índice**, no la memoria del proyecto. El conocimiento vive en `docs/`.

## Al empezar una sesión importante, leer
1. `docs/PROJECT_STATUS.md`: estado actual, fase en curso y problemas conocidos.
2. `docs/ROADMAP.md`: qué está hecho, en curso, pendiente o bloqueado.
3. `docs/DECISIONS.md`: decisiones vigentes y su porqué.
4. El documento de la fase actual, en `docs/phases/`.

Convención de documentación (qué actualizar después de cada tarea, auditorías inmutables en
`docs/audits/`, investigaciones en `docs/investigations/`): `docs/README.md`.

## Reglas duras
- **Solo paper betting.** No añadir nunca código capaz de enviar, modificar ni cancelar órdenes;
  los tests de `tests/test_no_real_betting_surface.py` deben seguir en verde.
- No leer, modificar ni mostrar el `.env`, ni certificados ni claves. No escribir secretos en la
  documentación.
- No tocar la base operativa (`DATABASE_URL`) salvo petición explícita. Para analizar, usar
  sesiones de solo lectura.
- No reiniciar, parar ni reconfigurar `edgecourt-collector.service` sin autorización.
- No cambiar una decisión de `docs/DECISIONS.md` en silencio: registrar una nueva que la
  sustituya.
- Las migraciones aplicadas no se editan; los cambios van en una migración nueva.

## Comandos
```bash
uv run pytest                    # suite completa (la integración requiere EDGECOURT_TEST_DSN)
uv run pytest -m critical        # tests que bloquean el avance de fase
uv run ruff check . && uv run ruff format --check .
uv run edgecourt collector health
```
