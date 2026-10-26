# SmartSQL Schema Designer

SmartSQL Schema Designer is a local-first Streamlit application that profiles uploaded tabular files, recommends schema types and candidate keys, surfaces evidence for possible foreign keys, validates the reviewed schema against source rows, and generates PostgreSQL DDL plus downloadable reports.

## Current implementation

- Multiple-file upload; CSV, TSV/TXT, XLSX, XLSM, XLS, JSON, JSONL/NDJSON, Parquet, XML and ZIP archives. XLS and Parquet reader paths and tests are included, but those two test cases were skipped in the build container because `xlrd`/`xlwt` and `pyarrow` were not installed there; install `requirements.txt` to run them.
- Excel workbooks are read worksheet-by-worksheet. Macros are not run.
- Data profiling: row/column counts, missing values, distinct and repeated values, duplicate complete rows, simple numeric ranges/outliers, samples and warnings.
- Conservative SQL data-type suggestions, including preservation of identifier-like values and leading zeros, explicit exceptions, and ambiguous slash-date warnings.
- Single-column and pairwise composite primary-key candidates with evidence. A clean identifier-named candidate can be preselected as a suggestion but remains editable; arbitrary unique text fields are not silently promoted.
- Evidence-based cross-dataset relationship candidates with name similarity, value overlap, row coverage, unmatched source rows, confidence, observed cardinality and user approval.
- A lightweight SVG Entity Relationship Diagram and dependency-aware table/data-load order.
- Editable target table/column names, SQL types, nullable flags, primary keys, unique constraints and column inclusion.
- PostgreSQL DDL generation with safe identifier quoting, stable constraint names, composite primary keys and approved foreign keys added after table creation. No cascading deletes/updates are generated.
- Downloadable `schema.sql`, `schema_report.json`, `schema_report.csv`, `relationship_report.csv`, `validation_report.csv`, `source_mapping.csv`, `normalization_opportunities.csv`, `README.txt`, `erd.svg`, plus a ZIP of all generated outputs.
- Automated tests for file parsing, type inference, keys, relationships, validation and SQL generation. Core test result in the supplied build environment: 43 passed; 2 skipped due to unavailable optional parser/test-engine packages.

## Explicit v0.1.0 limitations

- **PostgreSQL is the only fully implemented SQL DDL dialect.** MySQL and SQL Server are not offered as selectable targets and SQLite-specific generation has not been implemented; this avoids emitting a misleading dialect conversion.
- **Relational Schema Design preserves source datasets as tables.** It surfaces relationship, repeated-value, duplicate-attribute, shared-domain and many-to-many clues but does not automatically split source tables into normalised entities. Such decomposition needs business rules and explicit source-to-target transformation mapping.
- Automatic relationship detection is a review aid. A candidate is not treated as a business fact, and an FK cannot be approved if the sample contains unmatched non-null child references, the parent column is not marked PK/UNIQUE, a column is excluded, or SQL types are obviously incompatible.
- No database connection is opened; no source rows are loaded; no database object is changed. `schema.sql` creates structure only.
- XML support expects a repeating tabular structure that Pandas can interpret. ZIP parsing is shallow (nested ZIP files are not expanded) and limited to 50 supported members. Files are limited to 100 MiB each.
- The SVG ERD is an overview and can become crowded for many tables. Live PostgreSQL execution was not tested because no PostgreSQL server/client is required or contacted by this app. The Streamlit UI could not be runtime-launched inside the build environment because Streamlit was absent and package installation was blocked by the environment's network/DNS restrictions; the application source does pass Python compilation.
- Row-level issue lists are capped to protect memory on very dirty/large files; the report notes when a per-column or global cap is reached.

## Install

Python 3.11+ is recommended. From the project directory:

```bash
python -m venv .venv
```

Activate it:

**Windows PowerShell**

```powershell
.venv\Scripts\Activate.ps1
```

**macOS/Linux**

```bash
source .venv/bin/activate
```

Install dependencies and run tests:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest -q
```

Start the application:

```bash
streamlit run app.py
```

Streamlit will print a local URL (usually `http://localhost:8501`). Upload source files, or select **Load sample workspace** to run the workflow on the included synthetic CSV files.

## Suggested workflow

1. **Upload & Analyze** — choose one or multiple source files, or load the included sample data.
2. **Data Profile** — inspect all datasets/worksheets, deselect irrelevant datasets, inspect samples and key candidates.
3. **Schema Review** — choose Direct Import or Relational mode; edit target names, SQL types, column inclusion, nullability, PK and UNIQUE settings.
4. **Relationships & ERD** — examine evidence and unmatched values. Approve only valid relationships whose parent key and target types are appropriate.
5. **Validation Report** — inspect issues by severity, type and source row.
6. **SQL & Exports** — generate and download the PostgreSQL DDL and reports. The code block includes a copy control.

## Data safety and source lineage

Files are parsed in memory by the Streamlit process and are not sent to an external AI service. Uploaded files are never executed. The app does not connect to a database or execute generated SQL. Source column names and file/worksheet lineage are preserved in the source-mapping report. Blank values are treated as missing for profiling, while source values are not rewritten in the uploaded file.

## PostgreSQL import notes

`schema.sql` creates tables and selected constraints only. It does not insert/load source rows. Review `validation_report.csv` first.

- `COPY` is executed by the PostgreSQL server process, so the path must be visible to that server.
- `psql \\copy` is executed by the psql client, so the path must be visible to the client machine. Example:

```sql
\\copy public.students(student_id, student_name) FROM 'C:/data/students.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');
```

Load referenced parent tables before child tables. Do not enable cascading referential actions without a clear business rule.

## Project layout

```text
smartsql_schema_designer/
├── app.py
├── requirements.txt
├── README.md
├── modules/
│   ├── file_reader.py
│   ├── data_profiler.py
│   ├── type_inference.py
│   ├── key_detection.py
│   ├── relationship_detection.py
│   ├── schema_designer.py
│   ├── schema_validator.py
│   ├── sql_generator.py
│   ├── import_planner.py
│   ├── erd_generator.py
│   └── reporting.py
├── sample_data/
│   ├── students.csv
│   ├── courses.csv
│   └── enrollments.csv
├── output_examples/
│   ├── sample_schema.sql
│   ├── sample_schema_report.json
│   ├── sample_relationship_report.csv
│   └── sample_validation_report.csv
└── tests/
    ├── test_file_reader.py
    ├── test_type_inference.py
    ├── test_key_detection.py
    ├── test_relationship_detection.py
    ├── test_schema_validation.py
    └── test_sql_generator.py
```
