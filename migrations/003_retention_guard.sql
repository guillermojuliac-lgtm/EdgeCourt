-- EdgeCourt 003: control de archivado y proteccion contra purgas no verificadas.
--
-- Regla innegociable (decision del usuario): ninguna observacion se elimina de
-- PostgreSQL sin que exista antes una exportacion a Parquet verificada.
-- Esta tabla es el registro de esas verificaciones, y la funcion de purga se
-- niega a actuar si no encuentra una.

BEGIN;

CREATE TABLE archive_run (
    archive_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name      text NOT NULL,
    partition_name  text NOT NULL,
    period_start    date NOT NULL,
    period_end      date NOT NULL,
    exported_at     timestamptz NOT NULL DEFAULT now(),
    parquet_path    text NOT NULL,
    row_count_db    bigint NOT NULL,
    row_count_file  bigint NOT NULL,
    file_sha256     text NOT NULL,
    file_bytes      bigint NOT NULL,
    verified        boolean NOT NULL DEFAULT false,
    verified_at     timestamptz,
    purged_at       timestamptz,
    UNIQUE (table_name, partition_name),
    -- Solo se marca verificado si los recuentos cuadran exactamente.
    CONSTRAINT archive_verified_requires_matching_counts
        CHECK (NOT verified OR row_count_db = row_count_file),
    CONSTRAINT archive_verified_requires_timestamp
        CHECK ((verified_at IS NULL) = (NOT verified)),
    -- No se puede registrar una purga de algo no verificado.
    CONSTRAINT archive_purge_requires_verification
        CHECK (purged_at IS NULL OR verified)
);

COMMENT ON TABLE archive_run IS
    'Registro de exportaciones a Parquet. Una particion no puede purgarse sin una '
    'fila verificada aqui. Las restricciones lo imponen, no la disciplina.';

-- Guardia: se niega a purgar si no hay exportacion verificada.
CREATE OR REPLACE FUNCTION assert_archived(
    p_table text,
    p_partition text
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    ok boolean;
BEGIN
    SELECT verified INTO ok
    FROM archive_run
    WHERE table_name = p_table AND partition_name = p_partition;

    IF ok IS NULL THEN
        RAISE EXCEPTION
            'No existe exportacion registrada para %.%; purga abortada',
            p_table, p_partition;
    END IF;

    IF NOT ok THEN
        RAISE EXCEPTION
            'La exportacion de %.% no esta verificada; purga abortada',
            p_table, p_partition;
    END IF;
END;
$$;

COMMENT ON FUNCTION assert_archived IS
    'Lanza excepcion si la particion no tiene exportacion verificada.';

COMMIT;
