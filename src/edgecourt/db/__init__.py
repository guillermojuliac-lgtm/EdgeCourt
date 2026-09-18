"""Almacenamiento operativo en PostgreSQL.

PostgreSQL guarda el estado operativo 24/7: mercados de Betfair, observaciones,
precios, predicciones y paper bets. Parquet sigue siendo el almacen analitico e
historico, y el dataset de entrenamiento (matches, elo, features) no pasa por
aqui: son partidos inmutables que no ganan nada en una base de datos operativa.

Se usa psycopg 3 con SQL explicito, sin ORM, por coherencia con el resto del
proyecto: una dependencia menos y consultas que se leen tal cual se ejecutan.
"""
