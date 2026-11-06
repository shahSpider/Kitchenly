#!/bin/sh
# Runs once on first start of the bundled Postgres (compose). Creates the NON-superuser
# application role that owns the schema. Superusers bypass row-level security, so the
# app must never connect as one. In production create this role out-of-band instead.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE kitchenly LOGIN PASSWORD '${APP_DB_PASSWORD:-kitchenly}' NOSUPERUSER NOBYPASSRLS CREATEDB;
CREATE DATABASE kitchenly OWNER kitchenly;
SQL
