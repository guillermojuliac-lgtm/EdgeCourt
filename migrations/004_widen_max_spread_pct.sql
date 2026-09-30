-- EdgeCourt 004: amplia market_observation.max_spread_pct de numeric(8,4) a numeric(12,4).
--
-- Incidente del 2026-09-29 (docs/audits/2026-09-spread-overflow-incident.md): el
-- mercado 1.263050661 produjo un spread del peor runner de 13.471-17.331 %, que
-- no cabe en numeric(8,4) (maximo 9.999,9999). Cada INSERT fallaba con
-- "numeric field overflow" y abortaba el ciclo: 19 ciclos fallidos en ~86 min.
--
-- Por que numeric(12,4) y no otra cosa:
--
-- * Cota real del dato: la escala de precios de Betfair va de 1,01 a 1.000, asi
--   que el peor spread posible es (1000 - 1,01) / 1,01 * 100 = 98.909,9010 %.
--   numeric(12,4) admite hasta 99.999.999,9999: mas de 1.000 veces esa cota.
-- * Se conserva la escala de 4 decimales: los valores ya guardados no cambian y
--   las consultas y exportaciones siguen viendo el mismo tipo de dato.
-- * double precision descartado: introduciria redondeo binario en un dato que
--   hoy es decimal exacto.
-- * numeric sin precision descartado: perderia la escala fija y dejaria de
--   documentar el rango esperado.
--
-- Sin clamp: el valor real se guarda tal cual. Si el spread no es calculable
-- (sin BACK y LAY validos), sigue siendo NULL, como hasta ahora.
--
-- Ampliar la precision de un numeric sin cambiar la escala no reescribe la
-- tabla en PostgreSQL. En una tabla particionada, el ALTER sobre la tabla padre
-- se propaga a todas sus particiones.
--
-- Transaccionalidad: la gestiona el runner de migraciones. NO incluir
-- BEGIN/COMMIT aqui.


ALTER TABLE market_observation
    ALTER COLUMN max_spread_pct TYPE numeric(12,4);

COMMENT ON COLUMN market_observation.max_spread_pct IS
    'Peor spread relativo (lay-back)/back*100 entre runners con precio en ambos lados. '
    'numeric(12,4) desde la migracion 004: la cota real es 98.909,9010 %. NULL si no es calculable.';
