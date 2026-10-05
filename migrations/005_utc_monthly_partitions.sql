-- EdgeCourt 005: particionado mensual en UTC y reparacion del incidente del 2026-10-01.
--
-- Incidente (docs/audits/2026-10-partition-timezone-incident.md): desde el
-- 2026-10-01 00:00:42 UTC el collector no persistio nada. Cada ciclo fallaba en
-- ensure_month_partition con
--   "updated partition constraint for default partition would be violated".
--
-- Causa: la migracion 002 creaba las particiones con FROM (%L) TO (%L) sobre
-- fechas sin hora ni zona, que PostgreSQL interpreta en la zona horaria de la
-- SESION (Europe/Madrid). La de septiembre quedo como
--   [2026-08-31 22:00 UTC, 2026-09-30 22:00 UTC)
-- mientras el collector elige el mes en UTC. Entre las 22:00 y las 00:00 UTC del
-- 30-sep, 32 observaciones (y 64 precios) cayeron en la particion por defecto sin
-- error. A las 00:00 UTC el collector intento crear octubre con los limites de
-- Madrid, que empiezan a las 22:00 UTC del 30-sep y solapan con esas filas.
--
-- POLITICA (DEC-019): toda particion mensual es
--   [YYYY-MM-01 00:00:00 UTC, mes siguiente 00:00:00 UTC)
-- sin depender de la zona del servidor, de la sesion, de Europe/Madrid ni del
-- horario de verano.
--
-- Esta migracion:
--   1. define los limites UTC y sustituye ensure_month_partition;
--   2. repara, de forma transaccional, un esquema con limites no UTC o con filas
--      atrapadas en las particiones por defecto, sin perder ni modificar ninguna
--      fila existente. Si cualquier verificacion falla, se revierte todo;
--   3. garantiza las particiones del mes actual y del siguiente (UTC).
--
-- Transaccionalidad: la gestiona el runner de migraciones. NO incluir
-- BEGIN/COMMIT aqui: cerrarian la transaccion externa y el registro en
-- schema_migration quedaria fuera de ella.


-- ---------------------------------------------------------------------------
-- 1. Limites UTC
-- ---------------------------------------------------------------------------

-- timestamp (sin zona) AT TIME ZONE 'UTC' no depende de la zona de la sesion, y
-- la suma de '1 month' se hace sobre timestamp sin zona, es decir, sin DST.
CREATE OR REPLACE FUNCTION utc_month_bounds(p_month date)
RETURNS TABLE (lower_bound timestamptz, upper_bound timestamptz)
LANGUAGE sql IMMUTABLE
AS $$
    SELECT date_trunc('month', p_month::timestamp) AT TIME ZONE 'UTC',
           (date_trunc('month', p_month::timestamp) + interval '1 month') AT TIME ZONE 'UTC'
$$;

COMMENT ON FUNCTION utc_month_bounds IS
    'Limites [inicio, fin) del mes UTC de la fecha dada. Independiente de la zona de la sesion.';

-- Limites reales de una particion por rango. pg_get_expr los escribe con el
-- desplazamiento de la zona de la sesion, pero siempre con desplazamiento
-- explicito, asi que el cast a timestamptz es inequivoco. Sin filas para la
-- particion DEFAULT.
CREATE OR REPLACE FUNCTION partition_bounds(p_partition regclass)
RETURNS TABLE (lower_bound timestamptz, upper_bound timestamptz)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    v_expr text;
    v_match text[];
BEGIN
    SELECT pg_get_expr(c.relpartbound, c.oid) INTO v_expr
    FROM pg_class c WHERE c.oid = p_partition;

    v_match := regexp_match(v_expr, 'FROM \(''([^'']+)''\) TO \(''([^'']+)''\)');
    IF v_match IS NULL THEN
        RETURN;
    END IF;
    lower_bound := v_match[1]::timestamptz;
    upper_bound := v_match[2]::timestamptz;
    RETURN NEXT;
END;
$$;

COMMENT ON FUNCTION partition_bounds IS
    'Limites [inicio, fin) de una particion por rango como timestamptz absolutos.';

CREATE OR REPLACE FUNCTION ensure_month_partition(
    parent_table text,
    month_start  date
) RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_lower  timestamptz;
    v_upper  timestamptz;
    v_name   text;
    v_found  regclass;
    v_have   record;
BEGIN
    SELECT b.lower_bound, b.upper_bound INTO v_lower, v_upper
    FROM utc_month_bounds(month_start) b;

    v_name := format('%s_%s', parent_table, to_char(date_trunc('month', month_start::timestamp), 'YYYYMM'));
    v_found := to_regclass(format('%I', v_name));

    IF v_found IS NULL THEN
        -- Literales con desplazamiento +00 explicito: no dependen de la sesion.
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
            v_name, parent_table,
            to_char(v_lower AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '+00',
            to_char(v_upper AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '+00'
        );
    ELSE
        SELECT pb.lower_bound, pb.upper_bound INTO v_have FROM partition_bounds(v_found) pb;
        IF v_have.lower_bound IS DISTINCT FROM v_lower OR v_have.upper_bound IS DISTINCT FROM v_upper THEN
            RAISE WARNING
                'la particion % no tiene los limites UTC esperados [%, %): tiene [%, %)',
                v_name, v_lower, v_upper, v_have.lower_bound, v_have.upper_bound;
        END IF;
    END IF;
    RETURN v_name;
END;
$$;

COMMENT ON FUNCTION ensure_month_partition IS
    'Crea la particion del mes UTC si no existe. Limites [mes 00:00 UTC, mes siguiente 00:00 UTC). Idempotente.';


-- ---------------------------------------------------------------------------
-- 2. Reparacion transaccional
-- ---------------------------------------------------------------------------

DO $repair$
DECLARE
    v_rec          record;
    v_new          record;
    v_old          record;
    v_month        date;
    v_obs_total    bigint;
    v_rp_total     bigint;
    v_obs_default  bigint;
    v_rp_default   bigint;
    v_obs_md5      text;
    v_rp_md5       text;
    v_md5_after    text;
    v_outside      bigint;
    v_count_after  bigint;
    v_fk_name      text;
    v_fk_def       text;
    v_fk_dropped   boolean := false;
BEGIN
    -- Nadie escribe mientras se reorganiza el esquema. Se mantiene hasta el COMMIT.
    LOCK TABLE market_observation, runner_price IN ACCESS EXCLUSIVE MODE;

    -- Particiones mensuales cuyos limites NO son el mes UTC exacto.
    CREATE TEMP TABLE _repair_partitions (
        parent text, child text, new_lower timestamptz, new_upper timestamptz
    ) ON COMMIT DROP;

    FOR v_rec IN
        SELECT p.relname::text AS parent, c.oid AS child_oid, c.relname::text AS child,
               regexp_match(c.relname, '_([0-9]{4})([0-9]{2})$') AS ym
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname IN ('market_observation', 'runner_price')
    LOOP
        CONTINUE WHEN v_rec.ym IS NULL;   -- la particion DEFAULT no tiene limites
        SELECT b.lower_bound, b.upper_bound INTO v_new
        FROM utc_month_bounds(make_date(v_rec.ym[1]::int, v_rec.ym[2]::int, 1)) b;
        SELECT pb.lower_bound, pb.upper_bound INTO v_old FROM partition_bounds(v_rec.child_oid) pb;

        IF v_old.lower_bound IS DISTINCT FROM v_new.lower_bound
           OR v_old.upper_bound IS DISTINCT FROM v_new.upper_bound THEN
            INSERT INTO _repair_partitions VALUES (v_rec.parent, v_rec.child, v_new.lower_bound, v_new.upper_bound);
        END IF;
    END LOOP;

    SELECT count(*) INTO v_obs_default FROM market_observation_default;
    SELECT count(*) INTO v_rp_default  FROM runner_price_default;

    IF v_obs_default = 0 AND v_rp_default = 0
       AND NOT EXISTS (SELECT 1 FROM _repair_partitions) THEN
        RAISE NOTICE 'particionado ya en UTC y sin filas en default: nada que reparar';
        RETURN;
    END IF;

    SELECT count(*) INTO v_obs_total FROM market_observation;
    SELECT count(*) INTO v_rp_total  FROM runner_price;
    RAISE NOTICE 'reparando particionado: % observaciones y % precios en total; % y % atrapados en default; % particiones con limites no UTC',
        v_obs_total, v_rp_total, v_obs_default, v_rp_default, (SELECT count(*) FROM _repair_partitions);

    -- (1) PRESERVAR: copia de las filas atrapadas, dentro de esta transaccion. Hasta
    -- que no existe esta copia no se borra nada.
    CREATE TEMP TABLE _repair_obs ON COMMIT DROP AS SELECT * FROM market_observation_default;
    CREATE TEMP TABLE _repair_rp  ON COMMIT DROP AS SELECT * FROM runner_price_default;

    IF (SELECT count(*) FROM _repair_obs) <> v_obs_default OR (SELECT count(*) FROM _repair_rp) <> v_rp_default THEN
        RAISE EXCEPTION 'la copia de seguridad no coincide con las filas de default: reparacion abortada';
    END IF;

    SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t.observation_id, t.observed_at), '')) INTO v_obs_md5 FROM _repair_obs t;
    SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t.observation_id, t.observed_at, t.selection_id), '')) INTO v_rp_md5 FROM _repair_rp t;

    IF EXISTS (
        SELECT 1 FROM _repair_rp r
        WHERE NOT EXISTS (
            SELECT 1 FROM _repair_obs o
            WHERE o.observation_id = r.observation_id AND o.observed_at = r.observed_at
        )
    ) THEN
        RAISE EXCEPTION 'hay precios en default sin su observacion en default: reparacion abortada';
    END IF;

    -- (2) RETIRAR de default. Los precios caen en cascada con su observacion.
    DELETE FROM market_observation_default;
    IF EXISTS (SELECT 1 FROM runner_price_default) THEN
        RAISE EXCEPTION 'quedan precios en default tras retirar sus observaciones: reparacion abortada';
    END IF;

    -- (3a) Reajustar a UTC las particiones mensuales con limites de otra zona.
    -- Debe hacerse con default vacio: ATTACH comprueba que default no tenga filas
    -- dentro del nuevo rango.
    --
    -- PostgreSQL no deja desacoplar una particion de market_observation mientras
    -- runner_price la referencia por clave foranea ("removing partition ...
    -- violates foreign key constraint"). Se suelta la FK raiz, se reajusta, y se
    -- vuelve a crear con su definicion EXACTA (se revalida entera). Todo dentro de
    -- esta transaccion y con las tablas bloqueadas: nadie ve el hueco.
    IF EXISTS (SELECT 1 FROM _repair_partitions) THEN
        SELECT con.conname::text, pg_get_constraintdef(con.oid) INTO v_fk_name, v_fk_def
        FROM pg_constraint con
        WHERE con.conrelid = 'runner_price'::regclass
          AND con.confrelid = 'market_observation'::regclass
          AND con.contype = 'f'
          AND con.conparentid = 0;
        IF v_fk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE runner_price DROP CONSTRAINT %I', v_fk_name);
            v_fk_dropped := true;
        END IF;
    END IF;

    -- Primero se desacoplan TODAS las particiones mal acotadas y despues se
    -- reacoplan con limites UTC: si dos meses vecinos tenian limites de Madrid
    -- (p. ej. septiembre y octubre), reajustar uno a uno solaparia en el camino.
    FOR v_rec IN SELECT * FROM _repair_partitions ORDER BY parent, child LOOP
        EXECUTE format(
            'SELECT count(*) FROM %I WHERE observed_at < %L OR observed_at >= %L',
            v_rec.child, v_rec.new_lower, v_rec.new_upper
        ) INTO v_outside;
        IF v_outside > 0 THEN
            RAISE EXCEPTION
                'la particion % tiene % filas fuera de su mes UTC: requiere intervencion manual',
                v_rec.child, v_outside;
        END IF;
        EXECUTE format('ALTER TABLE %I DETACH PARTITION %I', v_rec.parent, v_rec.child);
    END LOOP;

    FOR v_rec IN SELECT * FROM _repair_partitions ORDER BY parent, child LOOP
        EXECUTE format(
            'ALTER TABLE %I ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)',
            v_rec.parent, v_rec.child, v_rec.new_lower, v_rec.new_upper
        );
    END LOOP;

    IF v_fk_dropped THEN
        EXECUTE format('ALTER TABLE runner_price ADD CONSTRAINT %I %s', v_fk_name, v_fk_def);
    END IF;

    -- (3b) Crear las particiones UTC de los meses de las filas retiradas.
    FOR v_month IN
        SELECT DISTINCT date_trunc('month', observed_at AT TIME ZONE 'UTC')::date FROM _repair_obs
    LOOP
        PERFORM ensure_month_partition('market_observation', v_month);
        PERFORM ensure_month_partition('runner_price', v_month);
    END LOOP;

    -- (4) REINSERTAR conservando identificadores y contenido. PostgreSQL las
    -- enruta a la particion que les corresponde.
    INSERT INTO market_observation OVERRIDING SYSTEM VALUE SELECT * FROM _repair_obs;
    INSERT INTO runner_price SELECT * FROM _repair_rp;

    -- (5) VERIFICAR. Cualquier fallo revierte la migracion entera.
    SELECT count(*) INTO v_count_after FROM market_observation;
    IF v_count_after <> v_obs_total THEN
        RAISE EXCEPTION 'market_observation: % filas antes y % despues', v_obs_total, v_count_after;
    END IF;
    SELECT count(*) INTO v_count_after FROM runner_price;
    IF v_count_after <> v_rp_total THEN
        RAISE EXCEPTION 'runner_price: % filas antes y % despues', v_rp_total, v_count_after;
    END IF;

    SELECT md5(coalesce(string_agg(m::text, '|' ORDER BY m.observation_id, m.observed_at), '')) INTO v_md5_after
    FROM market_observation m
    WHERE (m.observation_id, m.observed_at) IN (SELECT observation_id, observed_at FROM _repair_obs);
    IF v_md5_after IS DISTINCT FROM v_obs_md5 THEN
        RAISE EXCEPTION 'el contenido de las observaciones reinsertadas no coincide (md5)';
    END IF;

    SELECT md5(coalesce(string_agg(r::text, '|' ORDER BY r.observation_id, r.observed_at, r.selection_id), '')) INTO v_md5_after
    FROM runner_price r
    WHERE (r.observation_id, r.observed_at, r.selection_id) IN (SELECT observation_id, observed_at, selection_id FROM _repair_rp);
    IF v_md5_after IS DISTINCT FROM v_rp_md5 THEN
        RAISE EXCEPTION 'el contenido de los precios reinsertados no coincide (md5)';
    END IF;

    IF EXISTS (SELECT 1 FROM market_observation_default) OR EXISTS (SELECT 1 FROM runner_price_default) THEN
        RAISE EXCEPTION 'quedan filas en default tras la reparacion';
    END IF;

    FOR v_rec IN
        SELECT p.relname::text AS parent, c.oid AS child_oid, c.relname::text AS child,
               regexp_match(c.relname, '_([0-9]{4})([0-9]{2})$') AS ym
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname IN ('market_observation', 'runner_price')
    LOOP
        CONTINUE WHEN v_rec.ym IS NULL;
        SELECT b.lower_bound, b.upper_bound INTO v_new
        FROM utc_month_bounds(make_date(v_rec.ym[1]::int, v_rec.ym[2]::int, 1)) b;
        SELECT pb.lower_bound, pb.upper_bound INTO v_old FROM partition_bounds(v_rec.child_oid) pb;
        IF v_old.lower_bound IS DISTINCT FROM v_new.lower_bound OR v_old.upper_bound IS DISTINCT FROM v_new.upper_bound THEN
            RAISE EXCEPTION 'la particion % sigue sin limites UTC tras la reparacion', v_rec.child;
        END IF;
    END LOOP;

    RAISE NOTICE 'reparacion verificada: % observaciones y % precios reinsertados, contenido identico', v_obs_default, v_rp_default;
END;
$repair$;


-- ---------------------------------------------------------------------------
-- 3. Mes actual y siguiente (UTC)
-- ---------------------------------------------------------------------------

SELECT ensure_month_partition(t.name, m.month)
FROM (VALUES ('market_observation'), ('runner_price')) AS t(name),
     (VALUES ((now() AT TIME ZONE 'UTC')::date),
             ((date_trunc('month', now() AT TIME ZONE 'UTC') + interval '1 month')::date)) AS m(month);
