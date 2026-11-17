CREATE ROLE dealeros_app WITH LOGIN PASSWORD 'dealeros_local' CREATEDB;
GRANT CONNECT, CREATE ON DATABASE dealeros TO dealeros_app;
GRANT USAGE, CREATE ON SCHEMA public TO dealeros_app;
