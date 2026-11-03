-- EdgeCourt 006: vista del precio de cierre por mercado (Phase 3.5-B, Paso 1, DEC-021).
--
-- Solo CREA una vista. No modifica ni reescribe ninguna observacion, no cambia
-- tablas ni indices y no toca el collector.
--
-- Definicion (DEC-021, fijada antes de ver resultados de CLV):
--
--   Cierre de un mercado = la observacion con mayor observed_at entre las que
--   cumplen market_status = 'OPEN', inplay = false y has_prices, y cuyo
--   observed_at es anterior a la primera observacion del mercado con
--   inplay = true o market_status distinto de 'OPEN' (si existe).
--
--   Referencia de inicio = la primera observacion con inplay = true, si existe;
--   si no, el ultimo inicio publicado (betfair_market.market_start_time).
--   close_gap_min = minutos entre el cierre y esa referencia.
--   close_reliable = close_gap_min <= 5. Un cierre fuera de ese margen se marca
--   no fiable: no se descarta ni se sustituye.
--
--   Un mercado sin ninguna observacion pre-play con precios no tiene cierre
--   (no_close_reason = 'no_priced_prematch_observation').
--
-- El hito historico snapshot_label = 'close' NO es un cierre y esta vista no lo
-- usa. Los precios llegan con retraso (Delayed Key, 1-180 s): el cierre tiene un
-- ruido intrinseco de hasta unos 3 minutos.
--
-- Transaccionalidad: la gestiona el runner de migraciones. NO incluir BEGIN/COMMIT.

CREATE VIEW market_closing_observation AS
WITH leaving AS (
    SELECT market_id,
           min(observed_at)                          AS left_prematch_at,
           min(observed_at) FILTER (WHERE inplay)    AS first_inplay_at
    FROM market_observation
    WHERE inplay OR market_status <> 'OPEN'
    GROUP BY market_id
),
observed AS (
    SELECT DISTINCT market_id FROM market_observation
),
closing AS (
    SELECT DISTINCT ON (o.market_id)
           o.market_id,
           o.observation_id,
           o.observed_at,
           o.snapshot_label,
           o.minutes_to_start
    FROM market_observation o
    LEFT JOIN leaving l USING (market_id)
    WHERE o.market_status = 'OPEN'
      AND NOT o.inplay
      AND o.has_prices
      AND (l.left_prematch_at IS NULL OR o.observed_at < l.left_prematch_at)
    ORDER BY o.market_id, o.observed_at DESC, o.observation_id DESC
)
SELECT x.market_id,
       c.observation_id                                  AS closing_observation_id,
       c.observed_at                                     AS closing_observed_at,
       c.snapshot_label                                  AS closing_snapshot_label,
       c.minutes_to_start                                AS closing_minutes_to_start,
       ref.reference_start,
       ref.reference_kind,
       round((extract(epoch FROM ref.reference_start - c.observed_at) / 60.0)::numeric, 2)
                                                         AS close_gap_min,
       CASE WHEN c.observation_id IS NULL THEN NULL
            ELSE (extract(epoch FROM ref.reference_start - c.observed_at) / 60.0) <= 5
       END                                               AS close_reliable,
       CASE WHEN c.observation_id IS NULL THEN 'no_priced_prematch_observation' END
                                                         AS no_close_reason
FROM observed x
JOIN betfair_market m USING (market_id)
LEFT JOIN leaving l USING (market_id)
LEFT JOIN closing c USING (market_id)
CROSS JOIN LATERAL (
    SELECT COALESCE(l.first_inplay_at, m.market_start_time) AS reference_start,
           CASE WHEN l.first_inplay_at IS NOT NULL THEN 'inplay_observed'
                ELSE 'published_start' END                 AS reference_kind
) ref;

COMMENT ON VIEW market_closing_observation IS
    'Precio de cierre por mercado (DEC-021): ultima observacion pre-play con precios anterior '
    'a la primera senal de juego, con su distancia al inicio y su fiabilidad (<= 5 min).';
