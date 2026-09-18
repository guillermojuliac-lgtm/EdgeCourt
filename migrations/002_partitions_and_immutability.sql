-- EdgeCourt 002: particiones mensuales e inmutabilidad del ledger.

BEGIN;

-- ---------------------------------------------------------------------------
-- Particiones mensuales.
--
-- El particionado existe para poder archivar: DETACH + exportar a Parquet +
-- DROP es instantaneo y no deja la tabla bloqueada, a diferencia de un DELETE
-- masivo que ademas dejaria la tabla hinchada hasta el siguiente VACUUM FULL.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION ensure_month_partition(
    parent_table text,
    month_start  date
) RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    partition_name text;
    month_end      date;
BEGIN
    month_start := date_trunc('month', month_start)::date;
    month_end   := (month_start + interval '1 month')::date;
    partition_name := format('%s_%s', parent_table, to_char(month_start, 'YYYYMM'));

    IF NOT EXISTS (
        SELECT 1 FROM pg_class WHERE relname = partition_name
    ) THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
            partition_name, parent_table, month_start, month_end
        );
    END IF;

    RETURN partition_name;
END;
$$;

COMMENT ON FUNCTION ensure_month_partition IS
    'Crea la particion mensual si no existe. Idempotente.';

-- Particiones por defecto: sin ellas, una insercion fuera de rango falla.
-- Se vigilan: filas aqui significan que el creador de particiones no se ejecuto.
CREATE TABLE market_observation_default PARTITION OF market_observation DEFAULT;
CREATE TABLE runner_price_default PARTITION OF runner_price DEFAULT;

-- ---------------------------------------------------------------------------
-- Inmutabilidad del ledger.
--
-- Tres capas, porque una sola no basta:
--   1. la aplicacion nunca emite UPDATE ni DELETE sobre estas tablas;
--   2. las reglas convierten en no-op cualquier intento;
--   3. la cadena de hashes hace detectable una manipulacion directa.
-- ---------------------------------------------------------------------------

CREATE RULE paper_bet_no_update AS ON UPDATE TO paper_bet DO INSTEAD NOTHING;
CREATE RULE paper_bet_no_delete AS ON DELETE TO paper_bet DO INSTEAD NOTHING;

CREATE RULE bet_settlement_no_update AS ON UPDATE TO bet_settlement DO INSTEAD NOTHING;
CREATE RULE bet_settlement_no_delete AS ON DELETE TO bet_settlement DO INSTEAD NOTHING;

-- ---------------------------------------------------------------------------
-- Vistas de analisis.
-- ---------------------------------------------------------------------------

-- La consulta que decidira MINIMUM_LIQUIDITY con datos en lugar de por intuicion.
CREATE VIEW liquidity_emergence AS
SELECT
    width_bucket(minutes_to_start, 0, 1440, 48) AS bucket,
    round(min(minutes_to_start), 1)             AS minutos_antes_min,
    round(max(minutes_to_start), 1)             AS minutos_antes_max,
    count(*)                                    AS observaciones,
    count(DISTINCT market_id)                   AS mercados,
    round(avg(has_prices::int)::numeric, 4)     AS pct_con_precios,
    round(avg(has_liquidity::int)::numeric, 4)  AS pct_con_liquidez,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY total_available)::numeric, 2)
                                                AS liquidez_mediana,
    round(percentile_cont(0.9) WITHIN GROUP (ORDER BY total_available)::numeric, 2)
                                                AS liquidez_p90
FROM market_observation
GROUP BY 1
ORDER BY 1;

COMMENT ON VIEW liquidity_emergence IS
    'Cuando aparece la liquidez respecto a market_start_time. Base empirica para '
    'fijar MINIMUM_LIQUIDITY y para decidir si los hitos lejanos merecen la pena.';

-- Cobertura por hito, distinguiendo capturas vacias de capturas con precios.
CREATE VIEW snapshot_coverage AS
SELECT
    snapshot_label,
    count(*)                                   AS observaciones,
    count(DISTINCT market_id)                  AS mercados,
    sum(has_prices::int)                       AS con_precios,
    sum(has_liquidity::int)                    AS con_liquidez,
    round(avg(has_prices::int)::numeric, 4)    AS pct_con_precios
FROM market_observation
GROUP BY snapshot_label;

COMMIT;
