SmartSQL Schema Designer — generated PostgreSQL schema outputs

1. Review schema.sql and validation_report.csv before importing source rows.
2. The SQL file creates database tables and approved constraints only; it does not connect to a database or load data.
3. Open psql or your preferred PostgreSQL client and execute the script in the intended database.
4. For a local file from the psql client, use client-side \copy, e.g.:
   \copy public.students(student_id, student_name) FROM '/path/to/students.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');
5. SQL COPY reads a path visible to the PostgreSQL server process; psql \copy reads a path visible to the client process.
6. Resolve missing/duplicate keys and unmatched foreign-key references before loading constrained tables.
7. Load parent tables before child tables. If a selected relationship cycle exists, load according to your data strategy and add constraints only after references are valid.
8. No database connection, credentials, source-file upload or data-loading operation was performed by this application.

Limitations in version 0.1.0:
- PostgreSQL is the only fully implemented DDL dialect.
- Relational mode does not automatically split source datasets into normalised tables; it preserves source mapping and surfaces evidence for review.
- Foreign keys are suggestions until explicitly approved. No cascading deletes/updates are generated.
