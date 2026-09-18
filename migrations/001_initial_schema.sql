-- EdgeCourt 001: esquema inicial del almacenamiento operativo.
--
-- Principio (docs/PERSISTENCE.md): PostgreSQL guarda el estado operativo 24/7;
-- Parquet sigue siendo el almacen analitico e historico. El dataset historico de
-- tenis (matches, elo, features) NO vive aqui: son 113.544 partidos inmutables
-- que no ganan nada en una base de datos operativa.
--
-- Distincion central: "mercado observado" y "snapshot con precios" son cosas
-- distintas y se modelan en tablas distintas. Un mercado OPEN con runners ACTIVE
-- y sin BACK/LAY es informacion valida, no una fila a medias.

BEGIN;

-- ---------------------------------------------------------------------------
-- Catalogo: cambia poco y se conserva siempre.
-- ---------------------------------------------------------------------------

CREATE TABLE betfair_event (
    event_id          text PRIMARY KEY,
    event_name        text NOT NULL,
    competition_id    text,
    competition_name  text,
    country_code      text,
    timezone          text,
    open_date         timestamptz,
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE betfair_event IS 'Partido/evento de Betfair. Nunca se purga.';

CREATE TABLE betfair_market (
    market_id          text PRIMARY KEY,
    event_id           text NOT NULL REFERENCES betfair_event(event_id),
    market_name        text NOT NULL,
    market_type        text NOT NULL DEFAULT 'MATCH_ODDS',
    market_start_time  timestamptz NOT NULL,
    first_seen_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at       timestamptz NOT NULL DEFAULT now(),
    final_status       text,
    settled_at         timestamptz
);

CREATE INDEX betfair_market_pending_start
    ON betfair_market (market_start_time)
    WHERE settled_at IS NULL;

CREATE INDEX betfair_market_event ON betfair_market (event_id);

CREATE TABLE betfair_runner (
    market_id      text NOT NULL REFERENCES betfair_market(market_id),
    selection_id   bigint NOT NULL,
    runner_name    text NOT NULL,
    sort_priority  smallint,
    handicap       numeric(6,2) NOT NULL DEFAULT 0,
    -- Emparejamiento con el dataset historico: se rellena en PHASE 8b.
    -- Nullable y sin clave foranea a proposito: un mapeo dudoso debe poder
    -- quedar vacio en lugar de adivinarse (riesgo R5 del plan).
    player_id      text,
    PRIMARY KEY (market_id, selection_id)
);

COMMENT ON COLUMN betfair_runner.player_id IS
    'NULL hasta PHASE 8b. Nunca se rellena con un emparejamiento difuso.';

-- ---------------------------------------------------------------------------
-- Serie temporal: observaciones del mercado y precios.
-- Particionadas por mes para poder archivar con DETACH + DROP.
-- ---------------------------------------------------------------------------

CREATE TABLE market_observation (
    observation_id      bigint GENERATED ALWAYS AS IDENTITY,
    market_id           text NOT NULL REFERENCES betfair_market(market_id),
    observed_at         timestamptz NOT NULL,
    capture_key         text NOT NULL,
    snapshot_label      text NOT NULL,
    minutes_to_start    numeric(10,2) NOT NULL,

    market_status       text NOT NULL,
    inplay              boolean NOT NULL,
    bet_delay           smallint,
    active_runners      smallint,
    total_matched       numeric(14,2),

    -- Distincion explicita observacion / precios / liquidez.
    -- has_liquidity significa "hay algo", NO "hay suficiente": el umbral de
    -- negocio (MINIMUM_LIQUIDITY) se aplica en consulta, para poder decidirlo
    -- empiricamente mas adelante sin haber perdido informacion al guardar.
    has_prices          boolean NOT NULL,
    has_liquidity       boolean NOT NULL,
    runners_with_prices smallint NOT NULL DEFAULT 0,
    total_available     numeric(14,2) NOT NULL DEFAULT 0,
    best_back_available numeric(14,2),
    best_lay_available  numeric(14,2),
    max_spread_pct      numeric(8,4),

    collector_run_id    uuid NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),

    -- observed_at forma parte de la PK porque PostgreSQL exige que la clave de
    -- particion este incluida en ella.
    PRIMARY KEY (observation_id, observed_at),
    CONSTRAINT observation_liquidity_implies_prices
        CHECK (NOT has_liquidity OR has_prices),
    CONSTRAINT observation_prices_imply_runners
        CHECK (NOT has_prices OR runners_with_prices > 0)
) PARTITION BY RANGE (observed_at);

COMMENT ON TABLE market_observation IS
    'Una fila cada vez que se observa un mercado, tenga precios o no.';

-- Idempotencia del collector: una captura por mercado y clave de captura.
CREATE UNIQUE INDEX market_observation_capture
    ON market_observation (market_id, capture_key, observed_at);

-- La consulta que decide cuando aparece la liquidez.
CREATE INDEX market_observation_liquidity_curve
    ON market_observation (minutes_to_start, has_liquidity);

CREATE INDEX market_observation_by_market
    ON market_observation (market_id, observed_at DESC);

CREATE TABLE runner_price (
    observation_id        bigint NOT NULL,
    observed_at           timestamptz NOT NULL,
    selection_id          bigint NOT NULL,
    runner_status         text NOT NULL,
    last_price_traded     numeric(10,3),
    runner_total_matched  numeric(14,2),

    back_price_1 numeric(10,3), back_size_1 numeric(14,2),
    back_price_2 numeric(10,3), back_size_2 numeric(14,2),
    back_price_3 numeric(10,3), back_size_3 numeric(14,2),
    lay_price_1  numeric(10,3), lay_size_1  numeric(14,2),
    lay_price_2  numeric(10,3), lay_size_2  numeric(14,2),
    lay_price_3  numeric(10,3), lay_size_3  numeric(14,2),

    PRIMARY KEY (observation_id, observed_at, selection_id),
    FOREIGN KEY (observation_id, observed_at)
        REFERENCES market_observation (observation_id, observed_at)
        ON DELETE CASCADE,
    -- Las cuotas de Betfair nunca bajan de 1.01.
    CONSTRAINT runner_price_back_valid
        CHECK (back_price_1 IS NULL OR back_price_1 >= 1.01),
    CONSTRAINT runner_price_lay_valid
        CHECK (lay_price_1 IS NULL OR lay_price_1 >= 1.01)
) PARTITION BY RANGE (observed_at);

COMMENT ON TABLE runner_price IS
    'Precios BACK/LAY. Solo existe fila cuando hubo precios que guardar.';

-- ---------------------------------------------------------------------------
-- Modelos, predicciones y paper betting.
-- ---------------------------------------------------------------------------

CREATE TABLE model_version (
    model_id           text PRIMARY KEY,
    model_type         text NOT NULL,
    slot               text NOT NULL
        CHECK (slot IN ('production', 'challenger', 'retired')),
    trained_at         timestamptz NOT NULL,
    train_first_year   smallint NOT NULL,
    train_last_year    smallint NOT NULL,
    n_train_rows       integer NOT NULL,
    feature_names      text[] NOT NULL,
    feature_hash       text NOT NULL,
    artifact_sha256    text NOT NULL,
    artifact_path      text NOT NULL,
    hyperparameters    jsonb NOT NULL DEFAULT '{}',
    validation_metrics jsonb NOT NULL DEFAULT '{}',
    promoted_at        timestamptz,
    promoted_by        text,
    notes              text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT promotion_requires_who
        CHECK ((promoted_at IS NULL) = (promoted_by IS NULL))
);

-- Como maximo un modelo en produccion. Lo garantiza la base de datos, no una
-- convencion: la promocion es manual y no puede ocurrir por accidente (§18).
CREATE UNIQUE INDEX model_version_single_production
    ON model_version ((slot)) WHERE slot = 'production';

CREATE TABLE prediction (
    prediction_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_id        text NOT NULL REFERENCES model_version(model_id),
    -- Referencia LOGICA, sin clave foranea: las observaciones se archivan a los
    -- 90 dias y las predicciones viven mas. Una FK dura impediria purgar.
    observation_id  bigint NOT NULL,
    observed_at     timestamptz NOT NULL,
    market_id       text NOT NULL REFERENCES betfair_market(market_id),
    selection_id    bigint NOT NULL,
    predicted_at    timestamptz NOT NULL DEFAULT now(),
    probability     numeric(8,6) NOT NULL CHECK (probability BETWEEN 0 AND 1),
    feature_hash    text NOT NULL,
    features        jsonb,
    UNIQUE (model_id, observation_id, selection_id)
);

CREATE INDEX prediction_by_model ON prediction (model_id, predicted_at DESC);
CREATE INDEX prediction_by_market ON prediction (market_id, selection_id);

CREATE TABLE paper_bet (
    paper_bet_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_id     bigint NOT NULL REFERENCES prediction(prediction_id),
    model_id          text NOT NULL REFERENCES model_version(model_id),
    market_id         text NOT NULL REFERENCES betfair_market(market_id),
    selection_id      bigint NOT NULL,
    placed_at         timestamptz NOT NULL DEFAULT now(),
    side              text NOT NULL CHECK (side IN ('BACK', 'LAY')),

    model_probability          numeric(8,6) NOT NULL,
    market_probability_raw     numeric(8,6) NOT NULL,
    market_probability_devig   numeric(8,6) NOT NULL,
    entry_price                numeric(10,3) NOT NULL CHECK (entry_price >= 1.01),
    available_size             numeric(14,2) NOT NULL,
    edge                       numeric(8,6) NOT NULL,
    expected_value             numeric(10,6) NOT NULL,
    expected_value_after_costs numeric(10,6) NOT NULL,
    stake                      numeric(12,2) NOT NULL CHECK (stake > 0),
    liability                  numeric(12,2) NOT NULL CHECK (liability >= 0),
    bankroll_before            numeric(14,2) NOT NULL,

    -- Libro de precios CONGELADO en el momento de la apuesta. Permite auditar y
    -- reconstruir la decision aunque la observacion original ya se haya
    -- archivado a Parquet y purgado de PostgreSQL.
    market_book_snapshot jsonb NOT NULL,

    -- Cadena de integridad: una manipulacion directa en la base de datos queda
    -- detectable aunque se hagan con permisos de superusuario.
    prev_hash text NOT NULL,
    row_hash  text NOT NULL,

    UNIQUE (prediction_id, side)
);

CREATE INDEX paper_bet_by_model ON paper_bet (model_id, placed_at DESC);
CREATE INDEX paper_bet_by_market ON paper_bet (market_id);

COMMENT ON TABLE paper_bet IS
    'Ledger inmutable. Solo admite INSERT: la liquidacion va en bet_settlement.';

-- La liquidacion es una tabla aparte para que paper_bet NUNCA se modifique.
-- Con una sola tabla habria que hacer UPDATE al conocer el resultado, y ahi se
-- cuela la posibilidad de reescribir una prediccion a posteriori (§15).
CREATE TABLE bet_settlement (
    paper_bet_id        bigint PRIMARY KEY REFERENCES paper_bet(paper_bet_id),
    settled_at          timestamptz NOT NULL DEFAULT now(),
    result              text NOT NULL CHECK (result IN ('won', 'lost', 'void')),
    gross_pnl           numeric(12,2) NOT NULL,
    commission          numeric(12,2) NOT NULL CHECK (commission >= 0),
    net_pnl             numeric(12,2) NOT NULL,
    closing_price       numeric(10,3),
    closing_probability numeric(8,6),
    clv                 numeric(8,6),
    prev_hash text NOT NULL,
    row_hash  text NOT NULL
);

CREATE INDEX bet_settlement_by_date ON bet_settlement (settled_at DESC);

CREATE TABLE match_result (
    market_id           text PRIMARY KEY REFERENCES betfair_market(market_id),
    winner_selection_id bigint,
    settled_at          timestamptz NOT NULL,
    final_status        text NOT NULL,
    void_reason         text,
    source              text NOT NULL
        CHECK (source IN ('betfair', 'historical', 'manual')),
    match_id            text
);

COMMENT ON COLUMN match_result.void_reason IS
    'Betfair anula mercados por retirada. Una apuesta anulada es void, no perdida: '
    'contarla como perdida sesgaria el ROI a la baja, e ignorarla al alza.';

COMMIT;
