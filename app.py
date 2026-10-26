"""SmartSQL Schema Designer — Streamlit entry point."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import json
import zipfile

import pandas as pd
import streamlit as st

from modules.data_profiler import profile_dataset, source_row_number
from modules.file_reader import SUPPORTED_SUFFIXES, read_uploaded_file
from modules.erd_generator import generate_erd_svg
from modules.import_planner import plan_import
from modules.key_detection import detect_candidate_keys
from modules.normalization_analysis import analyse_normalization_opportunities
from modules.relationship_detection import detect_relationships, many_to_many_patterns
from modules.reporting import generate_report_bundle
from modules.schema_designer import build_initial_schema
from modules.schema_validator import validate_schema
from modules.sql_generator import generate_postgres_sql

APP_DIR = Path(__file__).resolve().parent
SAMPLE_DIR = APP_DIR / "sample_data"

st.set_page_config(
    page_title="SmartSQL Schema Designer",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
:root { --ss-ink:#15263f; --ss-muted:#63738a; --ss-teal:#087f72; --ss-teal-soft:#e5f5f1; --ss-border:#dce4ec; --ss-paper:#ffffff; --ss-canvas:#f4f7fb; --ss-amber:#a96f12; }
html, body, [class*="css"] { font-family:'DM Sans',sans-serif; }
.stApp { background:var(--ss-canvas); color:var(--ss-ink); }
.block-container { padding-top:1.35rem; padding-bottom:3rem; max-width:1500px; }
[data-testid="stSidebar"] { background:#132743; border-right:1px solid #243b59; }
[data-testid="stSidebar"] * { color:#edf4fb; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color:#bbcadc; }
.ss-brand { display:flex; align-items:center; gap:12px; padding:8px 4px 24px; }
.ss-logo { height:42px; width:42px; border-radius:13px; background:linear-gradient(145deg,#20b69e,#08766c); color:white; display:flex; justify-content:center; align-items:center; font-family:Manrope,sans-serif; font-weight:800; font-size:21px; box-shadow:0 8px 18px rgba(2,153,129,.2); }
.ss-brand-title { font:800 16px Manrope,sans-serif; letter-spacing:-.3px; color:#fff; }
.ss-brand-sub { color:#9fb3c9; font-size:11px; letter-spacing:.8px; text-transform:uppercase; margin-top:2px; }
.ss-hero { background:linear-gradient(120deg,#132743 0%,#1e4160 100%); color:white; border-radius:19px; padding:25px 29px; margin-bottom:21px; box-shadow:0 10px 30px rgba(26,52,81,.09); position:relative; overflow:hidden; }
.ss-hero:after { content:""; position:absolute; width:260px; height:260px; border:1px solid rgba(255,255,255,.09); border-radius:50%; right:-70px; top:-150px; box-shadow:0 0 0 30px rgba(255,255,255,.025),0 0 0 65px rgba(255,255,255,.02); }
.ss-kicker { font-size:11px; font-weight:700; letter-spacing:1.5px; text-transform:uppercase; color:#76d9c4; }
.ss-hero h1 { font:800 clamp(23px,2.4vw,34px) Manrope,sans-serif; letter-spacing:-1.1px; margin:7px 0 7px; color:#fff; }
.ss-hero p { color:#c4d4e5; font-size:14px; margin:0; max-width:760px; line-height:1.6; }
.ss-section-title { font:800 21px Manrope,sans-serif; letter-spacing:-.5px; color:#172a45; margin:8px 0 3px; }
.ss-section-sub { color:#718198; font-size:13px; margin-bottom:15px; }
.ss-panel { background:#fff; border:1px solid var(--ss-border); border-radius:15px; padding:17px 19px; box-shadow:0 3px 14px rgba(26,47,74,.025); }
.ss-small { color:#75849a; font-size:12px; }
.ss-pill { display:inline-block; background:#e8f6f2; color:#087769; border-radius:99px; font-weight:700; padding:4px 9px; font-size:10px; letter-spacing:.6px; text-transform:uppercase; }
.ss-status { border-radius:12px; padding:13px 16px; font-size:13px; border:1px solid #d4e7e2; background:#f0faf7; color:#176c60; }
.ss-status.warn { border-color:#f0dfbd; background:#fff8e9; color:#895b10; }
.ss-status.error { border-color:#efcbd0; background:#fff2f3; color:#923b48; }
[data-testid="stMetric"] { background:#fff; border:1px solid var(--ss-border); border-radius:14px; padding:14px 16px; box-shadow:0 3px 14px rgba(26,47,74,.025); }
[data-testid="stMetricLabel"] { color:#718198; }
[data-testid="stMetricValue"] { font-family:Manrope,sans-serif; color:#172a45; font-weight:800; }
.stButton>button, .stDownloadButton>button { border-radius:10px; border:1px solid #cbd8e5; font-weight:700; min-height:41px; }
.stButton>button[kind="primary"] { background:#087f72; border-color:#087f72; color:#fff; }
.stButton>button[kind="primary"]:hover { background:#066b60; border-color:#066b60; }
.stTabs [data-baseweb="tab-list"] { gap:9px; border-bottom:1px solid #dfe7ef; }
.stTabs [data-baseweb="tab"] { background:#edf2f7; border-radius:8px 8px 0 0; padding:9px 13px; color:#607189; }
.stTabs [aria-selected="true"] { background:#fff !important; color:#087f72 !important; font-weight:700; }
[data-testid="stFileUploader"] { background:#fff; border:1.5px dashed #aac5d4; border-radius:16px; padding:10px; }
[data-testid="stDataFrame"] { border:1px solid #e1e8f0; border-radius:11px; overflow:hidden; }
hr { border-color:#e0e7ef; }
code { color:#087f72; }
@media (max-width:700px) { .block-container { padding:1rem .8rem 2rem; } .ss-hero { padding:20px; } }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------- State and core helpers ----------
def _go_to(page_name: str) -> None:
    st.session_state["current_page"] = page_name
    st.session_state["navigation_radio"] = page_name


def _sync_navigation() -> None:
    st.session_state["current_page"] = st.session_state["navigation_radio"]


def _init_state() -> None:
    defaults = {
        "datasets": [],
        "active_dataset_ids": [],
        "tables": [],
        "relationships": [],
        "issues": [],
        "analysis_ready": False,
        "design_mode": "Direct Import Schema",
        "schema_name": "public",
        "last_bundle": None,
        "parse_errors": [],
        "current_page": "Upload & Analyze",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _active_datasets() -> list:
    active_ids = set(st.session_state.active_dataset_ids)
    return [dataset for dataset in st.session_state.datasets if dataset.dataset_id in active_ids]


def _recalculate(preserve_approvals: bool = True) -> None:
    datasets = _active_datasets()
    old_approval = {rel["id"]: bool(rel.get("approved", False)) for rel in st.session_state.relationships}
    st.session_state.relationships = detect_relationships(datasets, st.session_state.tables)
    if preserve_approvals:
        for rel in st.session_state.relationships:
            rel["approved"] = bool(old_approval.get(rel["id"], False)) and bool(rel.get("valid_for_fk"))
            if rel["approved"]:
                eligible, _reason = _can_approve_relationship(rel)
                if not eligible:
                    rel["approved"] = False
            if not rel["approved"]:
                st.session_state[f"fk_approve_{rel['id']}"] = False
    st.session_state.issues = validate_schema(datasets, st.session_state.tables, st.session_state.relationships)
    st.session_state.last_bundle = None


def _analyse_file_list(uploaded_items: list[tuple[str, bytes]], *, has_header: bool = True) -> tuple[list, list[str]]:
    datasets = []
    errors = []
    seen_ids: set[str] = set()
    for filename, content in uploaded_items:
        try:
            parsed = read_uploaded_file(filename, content, has_header=has_header)
            for dataset in parsed:
                if dataset.dataset_id in seen_ids:
                    dataset.dataset_id = f"{dataset.dataset_id}_{len(seen_ids)+1}"
                seen_ids.add(dataset.dataset_id)
                datasets.append(dataset)
        except Exception as exc:
            errors.append(f"{filename}: {exc}")
    return datasets, errors


def _replace_analysis(datasets: list, errors: list[str]) -> None:
    st.session_state.datasets = datasets
    st.session_state.active_dataset_ids = [dataset.dataset_id for dataset in datasets]
    st.session_state.tables = build_initial_schema(datasets)
    st.session_state.relationships = []
    st.session_state.parse_errors = errors
    st.session_state.analysis_ready = bool(datasets)
    st.session_state.last_bundle = None
    if datasets:
        _recalculate(preserve_approvals=False)
    else:
        st.session_state.issues = []


def _title(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="ss-section-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="ss-section-sub">{subtitle}</div>', unsafe_allow_html=True)


def _severity_summary(issues: list[dict]) -> tuple[int, int, int]:
    return (
        sum(issue.get("severity") == "ERROR" for issue in issues),
        sum(issue.get("severity") == "WARNING" for issue in issues),
        sum(issue.get("severity") == "INFO" for issue in issues),
    )


def _table_for_id(table_id: str) -> dict | None:
    return next((table for table in st.session_state.tables if table["id"] == table_id), None)


def _can_approve_relationship(rel: dict) -> tuple[bool, str]:
    if not rel.get("valid_for_fk"):
        return False, "Unmatched child references must be resolved first."
    child = _table_for_id(rel["child_table_id"])
    parent = _table_for_id(rel["parent_table_id"])
    if not child or not parent:
        return False, "A referenced table is not selected."
    child_col = next((c for c in child["columns"] if c["source_name"] == rel["child_column_source"] and c.get("included", True)), None)
    parent_col = next((c for c in parent["columns"] if c["source_name"] == rel["parent_column_source"] and c.get("included", True)), None)
    if not child_col or not parent_col:
        return False, "Include both source columns in Schema Review before approving."
    parent_pk_cols = [col for col in parent["columns"] if col.get("included", True) and col.get("primary_key")]
    parent_is_single_pk = len(parent_pk_cols) == 1 and parent_pk_cols[0]["source_name"] == parent_col["source_name"]
    if not parent_is_single_pk and not parent_col.get("unique"):
        return False, "Select the parent column as a single-column Primary Key or Unique column in Schema Review. A single column that is only part of a composite key cannot be referenced by this FK implementation."
    if not _types_compatible(child_col.get("sql_type", "TEXT"), parent_col.get("sql_type", "TEXT")):
        return False, "Child and parent SQL types are not obviously compatible; align them in Schema Review."
    return True, "Parent key is unique, column mappings are included, and no unmatched child references were observed."


def _types_compatible(left: str, right: str) -> bool:
    def group(value: str) -> str:
        value = " ".join(str(value).strip().upper().split())
        if value in {"TEXT", "VARCHAR", "CHAR"} or value.startswith(("VARCHAR(", "CHAR(")):
            return "TEXT"
        if value in {"SMALLINT", "INTEGER", "INT", "BIGINT"}:
            return "INTEGER"
        if value.startswith(("NUMERIC", "DECIMAL", "REAL", "DOUBLE PRECISION")):
            return "NUMERIC"
        return value
    return group(left) == group(right)


def _refresh_dataset_selection(new_ids: list[str]) -> None:
    all_datasets = st.session_state.datasets
    old_tables = {table["id"]: table for table in st.session_state.tables}
    selected_datasets = [dataset for dataset in all_datasets if dataset.dataset_id in set(new_ids)]
    missing_ids = {dataset.dataset_id for dataset in selected_datasets} - set(old_tables)
    if missing_ids:
        new_tables = build_initial_schema([dataset for dataset in selected_datasets if dataset.dataset_id in missing_ids])
        old_tables.update({table["id"]: table for table in new_tables})
    st.session_state.active_dataset_ids = [dataset.dataset_id for dataset in selected_datasets]
    st.session_state.tables = [old_tables[dataset.dataset_id] for dataset in selected_datasets]
    _recalculate(preserve_approvals=True)


def _build_bundle() -> dict[str, bytes]:
    active = _active_datasets()
    return generate_report_bundle(
        active,
        st.session_state.tables,
        st.session_state.relationships,
        st.session_state.issues,
        design_mode=st.session_state.design_mode,
        schema_name=st.session_state.schema_name.strip() or None,
    )


def _zip_bundle(bundle: dict[str, bytes]) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, content in sorted(bundle.items()):
            archive.writestr(filename, content)
    return stream.getvalue()


_init_state()

# ---------- Sidebar navigation ----------
with st.sidebar:
    st.markdown(
        '<div class="ss-brand"><div class="ss-logo">S</div><div><div class="ss-brand-title">SmartSQL</div><div class="ss-brand-sub">Schema Designer</div></div></div>',
        unsafe_allow_html=True,
    )
    st.markdown("<div style='font-size:10px;font-weight:700;letter-spacing:1.6px;color:#8ea6bf;margin:0 0 8px 4px;'>WORKFLOW</div>", unsafe_allow_html=True)
    pages = ["Upload & Analyze", "Data Profile", "Schema Review", "Relationships & ERD", "Validation Report", "SQL & Exports"]
    current = st.radio("Workflow stages", pages, index=pages.index(st.session_state.current_page) if st.session_state.current_page in pages else 0, label_visibility="collapsed", key="navigation_radio", on_change=_sync_navigation)
    st.session_state.current_page = current
    st.markdown("<hr style='border-color:#2b4260;margin:22px 0 16px;'>", unsafe_allow_html=True)
    if st.session_state.analysis_ready:
        active = _active_datasets()
        st.markdown(f"<div style='font-size:11px;color:#9fb3c9;'>CURRENT ANALYSIS</div><div style='font-size:22px;font-weight:800;color:white;margin-top:3px;'>{len(active)} dataset(s)</div><div style='font-size:12px;color:#b7c7d8;margin-top:3px;'>{sum(len(d.frame) for d in active):,} total rows</div>", unsafe_allow_html=True)
        error_count, warning_count, info_count = _severity_summary(st.session_state.issues)
        st.markdown(f"<div style='margin-top:14px;font-size:12px;color:#e5ecf3;'>● {error_count} errors &nbsp;&nbsp; ● {warning_count} warnings</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='font-size:12px;color:#9fb3c9;line-height:1.7;'>Upload one or more data files to begin. Analysis stays local in this app process.</div>", unsafe_allow_html=True)
    st.markdown("<div style='margin-top:30px;font-size:10px;line-height:1.7;color:#7e96b0;'>v0.1.0 · PostgreSQL first<br>Schema creation only · No database connection</div>", unsafe_allow_html=True)

# ---------- Page header ----------
page = st.session_state.current_page
hero_title = {
    "Upload & Analyze": "Design your schema before you load.",
    "Data Profile": "Understand the shape of your data.",
    "Schema Review": "Review every table and column.",
    "Relationships & ERD": "Make relationships evidence-led.",
    "Validation Report": "Find issues before they become import errors.",
    "SQL & Exports": "Generate your reviewed PostgreSQL schema.",
}[page]
hero_subtitle = {
    "Upload & Analyze": "Inspect CSV, Excel and other structured files, then turn actual data patterns into transparent schema recommendations.",
    "Data Profile": "See missing values, distinct counts, duplicates, data type evidence and suspicious values before committing to SQL types.",
    "Schema Review": "Rename targets, edit SQL types, choose keys and control nullability without writing SQL by hand.",
    "Relationships & ERD": "Review candidate foreign keys with value overlap, unmatched references, confidence and observed cardinality.",
    "Validation Report": "Inspect exact source rows for missing keys, duplicate identifiers, unsafe casts and orphaned references.",
    "SQL & Exports": "Download a deterministic CREATE TABLE script and the reports that explain what the app inferred.",
}[page]
st.markdown(f'<div class="ss-hero"><div class="ss-kicker">DATA ENGINEERING WORKSPACE · STEP {pages.index(page)+1} OF {len(pages)}</div><h1>{hero_title}</h1><p>{hero_subtitle}</p></div>', unsafe_allow_html=True)

if not st.session_state.analysis_ready and page != "Upload & Analyze":
    st.info("Start by uploading or loading sample data in **Upload & Analyze**. The remaining stages use the actual parsed datasets.")
    st.stop()

# ---------- Upload page ----------
if page == "Upload & Analyze":
    left, right = st.columns([1.65, 1], gap="large")
    with left:
        _title("01 · Upload source files", "Drop multiple files at once. Excel workbooks are expanded into separate worksheet datasets.")
        uploaded_files = st.file_uploader(
            "Drop files here or browse",
            type=["csv", "tsv", "txt", "xlsx", "xls", "xlsm", "json", "jsonl", "ndjson", "parquet", "xml", "zip"],
            accept_multiple_files=True,
            help="CSV, TSV, TXT, XLSX, XLS, XLSM, JSON, JSONL, NDJSON, Parquet, XML and ZIP archives. Maximum 100 MiB per file.",
        )
        st.markdown("<div class='ss-small'>100 MiB per file · Files are parsed in memory · Excel macros are not executed</div>", unsafe_allow_html=True)
        has_headers = st.checkbox("First row contains column headers (CSV and Excel)", value=True, help="Turn this off for headerless files; SmartSQL will assign column_1, column_2, … and preserve the first row as data.")
        analyze_col, sample_col = st.columns([1, 1])
        with analyze_col:
            run_analysis = st.button("Analyze uploaded files", type="primary", use_container_width=True, disabled=not bool(uploaded_files))
        with sample_col:
            load_sample = st.button("Load sample workspace", use_container_width=True)
        if run_analysis:
            upload_payload = [(item.name, item.getvalue()) for item in uploaded_files]
            with st.spinner("Parsing files and profiling datasets…"):
                parsed, errors = _analyse_file_list(upload_payload, has_header=has_headers)
                _replace_analysis(parsed, errors)
            if parsed:
                st.success(f"Analysed {len(parsed)} dataset(s) from {len(upload_payload)} uploaded file(s). Review the profiles before generating SQL.")
            else:
                st.error("No datasets could be parsed. Check the file types and parse errors below.")
        if load_sample:
            payload = []
            for path in sorted(SAMPLE_DIR.glob("*.csv")):
                payload.append((path.name, path.read_bytes()))
            with st.spinner("Loading the included synthetic sample files…"):
                parsed, errors = _analyse_file_list(payload)
                _replace_analysis(parsed, errors)
            if parsed:
                st.success("Sample data loaded. This is real input to the analysis pipeline; you can inspect and edit the resulting recommendations.")
                st.rerun()
        if st.session_state.parse_errors:
            with st.expander(f"Parsing warnings and errors ({len(st.session_state.parse_errors)})", expanded=True):
                for message in st.session_state.parse_errors:
                    st.error(message)
    with right:
        _title("Analysis pipeline", "From source files to an executable schema.")
        st.markdown("""
        <div class="ss-panel">
          <div style="display:flex;gap:12px;margin:8px 0 17px;align-items:flex-start"><div style="background:#e5f5f1;color:#087f72;border-radius:9px;padding:9px;font-weight:800">01</div><div><b>Parse & profile</b><div class="ss-small">Inspect all detected datasets and source worksheets.</div></div></div>
          <div style="display:flex;gap:12px;margin:8px 0 17px;align-items:flex-start"><div style="background:#e5f5f1;color:#087f72;border-radius:9px;padding:9px;font-weight:800">02</div><div><b>Infer types & keys</b><div class="ss-small">Use observed values, uniqueness, nulls and naming evidence.</div></div></div>
          <div style="display:flex;gap:12px;margin:8px 0 17px;align-items:flex-start"><div style="background:#e5f5f1;color:#087f72;border-radius:9px;padding:9px;font-weight:800">03</div><div><b>Review relationships</b><div class="ss-small">Inspect overlap and unmatched child references before approval.</div></div></div>
          <div style="display:flex;gap:12px;margin:8px 0 4px;align-items:flex-start"><div style="background:#e5f5f1;color:#087f72;border-radius:9px;padding:9px;font-weight:800">04</div><div><b>Generate SQL & reports</b><div class="ss-small">Download DDL, source mapping, validation and ERD.</div></div></div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<div class='ss-status warn' style='margin-top:14px'><b>Safety by design</b><br>SmartSQL does not connect to your database, execute uploaded SQL, run macros, or load source rows. SQL and reports are generated only after review.</div>", unsafe_allow_html=True)

    if st.session_state.analysis_ready:
        st.divider()
        _title("Current analysis", "These numbers are calculated from the files currently in session.")
        active = _active_datasets()
        error_count, warning_count, info_count = _severity_summary(st.session_state.issues)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Datasets", len(active))
        c2.metric("Source rows", f"{sum(len(d.frame) for d in active):,}")
        c3.metric("Proposed tables", len(st.session_state.tables))
        c4.metric("Errors", error_count)
        c5.metric("Warnings", warning_count)
        st.button("Continue to Data Profile →", type="primary", on_click=_go_to, args=("Data Profile",))

# ---------- Data Profile page ----------
elif page == "Data Profile":
    all_datasets = st.session_state.datasets
    selected_ids = set(st.session_state.active_dataset_ids)
    _title("Dataset selection", "Every worksheet is included by default. Exclude irrelevant sheets before schema review.")
    selection_rows = [{
        "Include": dataset.dataset_id in selected_ids,
        "Dataset": dataset.label,
        "Format": dataset.file_format,
        "Rows": len(dataset.frame),
        "Columns": len(dataset.frame.columns),
        "Dataset ID": dataset.dataset_id,
    } for dataset in all_datasets]
    selection_df = pd.DataFrame(selection_rows)
    edited_selection = st.data_editor(
        selection_df,
        hide_index=True,
        use_container_width=True,
        key="dataset_selection_editor",
        disabled=["Dataset", "Format", "Rows", "Columns", "Dataset ID"],
        column_config={"Include": st.column_config.CheckboxColumn("Include", help="Exclude irrelevant sheets/datasets from the schema."), "Dataset ID": None},
    )
    if st.button("Apply dataset selection", type="primary"):
        desired_ids = edited_selection.loc[edited_selection["Include"] == True, "Dataset ID"].tolist()  # noqa: E712
        if not desired_ids:
            st.error("Select at least one dataset to continue.")
        else:
            _refresh_dataset_selection(desired_ids)
            st.success("Dataset selection applied. Existing edits for retained tables were preserved.")
            st.rerun()

    active_datasets = _active_datasets()
    st.divider()
    _title("Dataset profile", "Column metrics distinguish repeated values in a single column from duplicated complete records.")
    dataset_labels = {dataset.label: dataset for dataset in active_datasets}
    chosen_label = st.selectbox("Inspect dataset", list(dataset_labels.keys()), key="profile_dataset_picker")
    dataset = dataset_labels[chosen_label]
    profile = profile_dataset(dataset)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows", f"{profile['row_count']:,}")
    m2.metric("Columns", profile["column_count"])
    m3.metric("Duplicate full rows", profile["duplicate_row_count"])
    m4.metric("Source size", f"{profile['size_bytes']/1024:.1f} KB")
    if profile["warnings"]:
        for warning in profile["warnings"]:
            st.warning(warning)
    column_profiles = []
    for col in profile["columns"]:
        source_column = dataset.frame[col["column"]]
        inference = next((c.get("inference", {}) for table in st.session_state.tables if table["id"] == dataset.dataset_id for c in table["columns"] if c["source_name"] == col["column"]), {})
        column_profiles.append({
            "Source column": col["column"],
            "Suggested SQL type": inference.get("sql_type", "TEXT"),
            "Confidence": inference.get("confidence", "Low"),
            "Missing": col["missing_count"],
            "Missing %": col["missing_pct"],
            "Distinct": col["distinct_count"],
            "Repeated values": col["duplicate_values"],
            "Constant": col["constant"],
            "Outliers": col["outlier_count"],
            "Sample values": " · ".join(col["sample_values"]),
            "Inference reason": inference.get("reason", ""),
            "Exceptions": ", ".join(map(str, inference.get("exceptions", []))),
        })
    profile_df = pd.DataFrame(column_profiles)
    st.dataframe(profile_df, use_container_width=True, hide_index=True, column_config={
        "Inference reason": st.column_config.TextColumn(width="large"),
        "Sample values": st.column_config.TextColumn(width="large"),
        "Exceptions": st.column_config.TextColumn(width="medium"),
    })
    with st.expander("Preview source rows (first 100)", expanded=False):
        preview = dataset.frame.head(100).copy()
        preview.insert(0, "_source_row", [source_row_number(dataset, int(i)) for i in preview.index])
        st.dataframe(preview, use_container_width=True, hide_index=True)
    with st.expander("Candidate primary keys", expanded=False):
        candidate_rows = []
        for candidate in detect_candidate_keys(dataset.frame):
            candidate_rows.append({
                "Columns": ", ".join(candidate["columns"]),
                "Kind": candidate["kind"],
                "Unique %": candidate["unique_pct"],
                "Missing": candidate["missing_count"],
                "Repeated values": candidate["duplicate_count"],
                "Unique and non-null in sample": candidate["valid_primary_key"],
                "Evidence": candidate["evidence"],
            })
        st.dataframe(pd.DataFrame(candidate_rows), use_container_width=True, hide_index=True)
    st.button("Continue to Schema Review →", type="primary", on_click=_go_to, args=("Schema Review",))

# ---------- Schema Review page ----------
elif page == "Schema Review":
    st.radio("Design mode", ["Direct Import Schema", "Relational Schema Design"], horizontal=True, key="design_mode")
    if st.session_state.design_mode == "Relational Schema Design":
        st.markdown("<div class='ss-status warn'><b>Relational mode:</b> SmartSQL reviews cross-dataset keys and surfaces many-to-many/normalization clues. It deliberately preserves source tables in this version and does not auto-split datasets without business rules.</div>", unsafe_allow_html=True)
        normalization_findings = analyse_normalization_opportunities(_active_datasets(), st.session_state.tables)
        st.markdown("**Normalisation opportunities to review**")
        if normalization_findings:
            st.dataframe(pd.DataFrame(normalization_findings), use_container_width=True, hide_index=True)
        else:
            st.caption("No structural normalisation clues met the current evidence thresholds. This is not proof that the data is fully normalised.")
    else:
        st.markdown("<div class='ss-status'>Direct import keeps one target table for each selected source dataset and preserves source-to-target mapping.</div>", unsafe_allow_html=True)
    active_tables = st.session_state.tables
    if not active_tables:
        st.warning("No selected tables are available.")
    else:
        selected_table_name = st.selectbox("Table to edit", [table["name"] for table in active_tables], key="schema_table_picker")
        table = next(table for table in active_tables if table["name"] == selected_table_name)
        _title(f"Table · {table['name']}", f"Source: {table['source_file']}{' / ' + table['source_sheet'] if table.get('source_sheet') else ''} · {table['row_count']:,} rows")
        new_table_name = st.text_input("Target table name", value=table["name"], key=f"target_table_name_{table['id']}", help="Names are quoted safely in PostgreSQL SQL; use clear, unique names where possible.")
        editor_rows = [{
            "Include": bool(col.get("included", True)),
            "Source column": col["source_name"],
            "Target column": col["name"],
            "SQL type": col["sql_type"],
            "Nullable": bool(col.get("nullable", True)),
            "Primary key": bool(col.get("primary_key", False)),
            "Unique": bool(col.get("unique", False)),
            "Confidence": col.get("inference", {}).get("confidence", "Low"),
        } for col in table["columns"]]
        edit_df = pd.DataFrame(editor_rows)
        st.caption("Select Primary Key on one column, or on multiple columns for a composite key. A PK is non-null by definition. Nullable is a future-data rule, not just an observation from this sample.")
        edited = st.data_editor(
            edit_df,
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            key=f"schema_editor_{table['id']}",
            column_config={
                "Include": st.column_config.CheckboxColumn("Include", help="Uncheck to exclude this source column."),
                "Source column": st.column_config.TextColumn("Source column", disabled=True),
                "Target column": st.column_config.TextColumn("Target column", help="Target column name in PostgreSQL."),
                "SQL type": st.column_config.TextColumn("SQL type", help="Examples: TEXT, INTEGER, NUMERIC(12,2), BOOLEAN, DATE, TIMESTAMP."),
                "Nullable": st.column_config.CheckboxColumn("Nullable"),
                "Primary key": st.column_config.CheckboxColumn("Primary key"),
                "Unique": st.column_config.CheckboxColumn("Unique"),
                "Confidence": st.column_config.TextColumn("Confidence", disabled=True),
            },
        )
        with st.expander("Why these SQL types were recommended", expanded=False):
            reason_rows = []
            for col in table["columns"]:
                meta = col.get("inference", {})
                reason_rows.append({"Source column": col["source_name"], "Type": col["sql_type"], "Reason": meta.get("reason", ""), "Confidence": meta.get("confidence", "Low"), "Exceptions": ", ".join(map(str, meta.get("exceptions", []))) or "None observed", "Ambiguous date": meta.get("ambiguous_date", False)})
            st.dataframe(pd.DataFrame(reason_rows), use_container_width=True, hide_index=True)
        if table.get("recommended_pk"):
            rec = table["recommended_pk"]
            st.markdown(f"<div class='ss-status'><b>Strong key candidate:</b> {', '.join(rec['columns'])} · {rec['evidence']} Review the selection in the editor above.</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='ss-status warn'><b>No safe existing primary key found.</b> Review candidate combinations or define a business-valid key. A surrogate key is not created automatically.</div>", unsafe_allow_html=True)
        if st.button("Apply table edits", type="primary"):
            columns_by_source = {col["source_name"]: col for col in table["columns"]}
            new_columns = []
            validation_error = None
            for _, row in edited.iterrows():
                original = columns_by_source[str(row["Source column"])]
                updated = deepcopy(original)
                updated["included"] = bool(row["Include"])
                updated["name"] = str(row["Target column"]).strip()
                updated["sql_type"] = str(row["SQL type"]).strip().upper()
                updated["nullable"] = bool(row["Nullable"])
                updated["primary_key"] = bool(row["Primary key"])
                updated["unique"] = bool(row["Unique"])
                if not updated["name"]:
                    validation_error = f"Target name for source column {updated['source_name']!r} cannot be blank."
                    break
                if not updated["sql_type"]:
                    validation_error = f"SQL type for source column {updated['source_name']!r} cannot be blank."
                    break
                new_columns.append(updated)
            if not new_table_name.strip():
                validation_error = "Target table name cannot be blank."
            if validation_error:
                st.error(validation_error)
            else:
                table["name"] = new_table_name.strip()
                table["columns"] = new_columns
                _recalculate(preserve_approvals=True)
                st.success("Schema edits applied. Relationships and validation have been recalculated.")
                st.rerun()
        st.divider()
        error_count, warning_count, info_count = _severity_summary(st.session_state.issues)
        m1, m2, m3 = st.columns(3)
        m1.metric("Current errors", error_count)
        m2.metric("Current warnings", warning_count)
        m3.metric("Information", info_count)
        st.button("Continue to Relationships →", type="primary", on_click=_go_to, args=("Relationships & ERD",))

# ---------- Relationships & ERD page ----------
elif page == "Relationships & ERD":
    relationships = st.session_state.relationships
    _title("Foreign-key candidates", "Candidates are discovered from data values and column meaning. A matching name alone never confirms a relationship.")
    if not relationships:
        st.info("No cross-dataset relationship candidates met the current evidence threshold. You can still generate independent tables and inspect the source profiles.")
    else:
        counts = {level: sum(rel["confidence"] == level for rel in relationships) for level in ["High", "Medium", "Low"]}
        a, b, c, d = st.columns(4)
        a.metric("Candidates", len(relationships))
        b.metric("High confidence", counts["High"])
        c.metric("Valid references", sum(rel["valid_for_fk"] for rel in relationships))
        d.metric("Approved", sum(rel.get("approved", False) for rel in relationships))
        for index, rel in enumerate(relationships):
            child = _table_for_id(rel["child_table_id"])
            parent = _table_for_id(rel["parent_table_id"])
            can_approve, reason = _can_approve_relationship(rel)
            title = f"{rel['child_table']}.{rel['child_column']}  →  {rel['parent_table']}.{rel['parent_column']} · {rel['confidence']} confidence"
            with st.expander(title, expanded=(index == 0)):
                left, right = st.columns([1.2, 1])
                with left:
                    st.markdown(f"**Evidence**  \n{rel['evidence']}")
                    ev1, ev2, ev3 = st.columns(3)
                    ev1.metric("Distinct value overlap", f"{rel['distinct_value_overlap_pct']:.1f}%")
                    ev2.metric("Row match coverage", f"{rel['matching_non_null_rows_pct']:.1f}%")
                    ev3.metric("Unmatched rows", rel["unmatched_row_count"])
                    st.caption(f"Observed cardinality: {rel['relationship_type']}. This describes the uploaded sample and is not a business-rule guarantee.")
                with right:
                    if rel["valid_for_fk"]:
                        st.markdown("<div class='ss-status'>No unmatched child values observed. The candidate may be constraint-ready if the parent key is selected and SQL types align.</div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div class='ss-status error'><b>Cannot enforce yet.</b><br>{rel['warning']}</div>", unsafe_allow_html=True)
                    if can_approve:
                        approved = st.checkbox("Approve this foreign key for SQL", value=bool(rel.get("approved", False)), key=f"fk_approve_{rel['id']}")
                        rel["approved"] = approved
                        st.caption(reason)
                    else:
                        rel["approved"] = False
                        st.checkbox("Approve this foreign key for SQL", value=False, disabled=True, key=f"fk_disabled_{rel['id']}")
                        st.caption(reason)
                if rel["unmatched_values"]:
                    st.markdown("**Unmatched source rows (first 500 listed)**")
                    st.dataframe(pd.DataFrame(rel["unmatched_values"]), use_container_width=True, hide_index=True)
        st.session_state.issues = validate_schema(_active_datasets(), st.session_state.tables, st.session_state.relationships)

    patterns = many_to_many_patterns(relationships)
    if patterns:
        st.divider()
        _title("Potential many-to-many patterns", "An associative-table pattern is a clue to review, not an automatically approved schema rewrite.")
        st.dataframe(pd.DataFrame(patterns), use_container_width=True, hide_index=True)

    st.divider()
    _title("Entity Relationship Diagram", "Solid green indicates an approved valid FK; dashed amber indicates a candidate awaiting review; red indicates unmatched references.")
    svg_text = generate_erd_svg(st.session_state.tables, st.session_state.relationships)
    st.components.v1.html(svg_text, height=min(1000, 400 + max(1, len(st.session_state.tables)) * 140), scrolling=True)
    st.download_button("Download ER diagram (SVG)", data=svg_text.encode("utf-8"), file_name="erd.svg", mime="image/svg+xml")
    plan = plan_import(st.session_state.tables, st.session_state.relationships)
    p1, p2 = st.columns(2)
    with p1:
        _title("Table creation order", "Generated from approved dependencies. FK constraints are added after table creation.")
        st.dataframe(pd.DataFrame(plan["creation_order"]), use_container_width=True, hide_index=True)
    with p2:
        _title("Data loading order", "Parent tables precede child tables wherever the approved graph is acyclic.")
        st.dataframe(pd.DataFrame(plan["data_load_order"]), use_container_width=True, hide_index=True)
        if plan["cycle_detected"]:
            st.warning(f"A dependency cycle was detected among: {', '.join(plan['cyclic_tables'])}. Load strategy needs review; constraints are generated after all tables.")
    st.button("Continue to Validation →", type="primary", on_click=_go_to, args=("Validation Report",))

# ---------- Validation page ----------
elif page == "Validation Report":
    st.session_state.issues = validate_schema(_active_datasets(), st.session_state.tables, st.session_state.relationships)
    issues = st.session_state.issues
    error_count, warning_count, info_count = _severity_summary(issues)
    a, b, c, d = st.columns(4)
    a.metric("Issues", len(issues))
    b.metric("Errors", error_count)
    c.metric("Warnings", warning_count)
    d.metric("Informational", info_count)
    if error_count:
        st.markdown(f"<div class='ss-status error'><b>Review required.</b> {error_count} error(s) indicate data that is likely to violate the currently selected schema or an approved constraint.</div>", unsafe_allow_html=True)
    elif warning_count:
        st.markdown(f"<div class='ss-status warn'><b>No blocking errors detected.</b> {warning_count} warning(s) still need attention or an explicit decision.</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='ss-status'><b>No schema validation errors were detected in the current source rows.</b> This does not guarantee that future data or business rules are valid.</div>", unsafe_allow_html=True)
    if issues:
        filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 2])
        severities = filter_col1.multiselect("Severity", ["ERROR", "WARNING", "INFO"], default=["ERROR", "WARNING", "INFO"])
        codes = sorted(set(issue.get("code", "") for issue in issues))
        selected_codes = filter_col2.multiselect("Issue type", codes, default=codes)
        search_text = filter_col3.text_input("Search table, column, value or message", value="")
        filtered = [issue for issue in issues if issue.get("severity") in severities and issue.get("code") in selected_codes]
        if search_text:
            needle = search_text.casefold()
            filtered = [issue for issue in filtered if needle in " ".join(str(value) for value in issue.values()).casefold()]
        st.caption(f"Showing {len(filtered):,} of {len(issues):,} issue rows. Source-row numbers are best-effort physical rows for CSV/Excel and record numbers for JSON-style inputs.")
        st.dataframe(pd.DataFrame(filtered), use_container_width=True, hide_index=True, height=480)
        st.download_button("Download current validation report", data=pd.DataFrame(issues).to_csv(index=False).encode("utf-8-sig"), file_name="validation_report.csv", mime="text/csv")
    else:
        st.success("No issues were detected by the implemented checks.")
    with st.expander("Validation coverage and limitations", expanded=False):
        st.markdown("""
        **Implemented checks:** missing and duplicate selected primary-key values, composite-key conflicts, duplicate full rows, required-field nulls, selected UNIQUE violations, SQL-type incompatibilities, ambiguous slash dates, leading-zero risk, inference exceptions, approved FK orphans, and naming conflicts.

        **Interpret with care:** a clean sample does not prove future values will follow the same rules. This version does not automatically generate surrogate keys, infer all business constraints, execute SQL against a live PostgreSQL server, or validate an import transaction.
        """)
    st.button("Continue to SQL & Exports →", type="primary", on_click=_go_to, args=("SQL & Exports",))

# ---------- SQL & Exports page ----------
elif page == "SQL & Exports":
    st.radio("Design mode", ["Direct Import Schema", "Relational Schema Design"], horizontal=True, key="design_mode")
    schema_name = st.text_input("PostgreSQL schema name (blank = default search path)", value=st.session_state.schema_name, key="schema_name_input")
    st.session_state.schema_name = schema_name
    active = _active_datasets()
    plan = plan_import(st.session_state.tables, st.session_state.relationships)
    error_count, warning_count, info_count = _severity_summary(st.session_state.issues)
    a, b, c, d = st.columns(4)
    a.metric("Datasets", len(active))
    a2 = len([t for t in st.session_state.tables if any(col.get("included", True) for col in t["columns"])])
    b.metric("Tables", a2)
    c.metric("Approved FKs", sum(rel.get("approved", False) for rel in st.session_state.relationships))
    d.metric("Validation errors", error_count)
    if error_count:
        st.markdown("<div class='ss-status warn'><b>SQL can still describe the selected structure, but data import is not ready.</b> Review the validation report. Approved foreign keys with unmatched records are blocked.</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='ss-status'>DDL generation is local and deterministic. It creates schema objects only; source data is not uploaded or inserted.</div>", unsafe_allow_html=True)
    if plan["cycle_detected"]:
        st.warning(f"Dependency cycle detected: {', '.join(plan['cyclic_tables'])}. The generator creates all tables first and then applies approved foreign-key constraints.")
    if st.button("Generate SQL and all reports", type="primary"):
        st.session_state.issues = validate_schema(active, st.session_state.tables, st.session_state.relationships)
        try:
            st.session_state.last_bundle = _build_bundle()
            st.success("SQL and reports generated from the current reviewed schema.")
        except Exception as exc:
            st.session_state.last_bundle = None
            st.error(f"SQL generation could not complete: {exc}")
    bundle = st.session_state.last_bundle
    if bundle:
        st.divider()
        _title("Output A · Schema SQL", "The main deliverable: PostgreSQL CREATE TABLE statements plus approved foreign keys.")
        sql_text = bundle["schema.sql"].decode("utf-8")
        st.code(sql_text, language="sql", line_numbers=True)
        st.download_button("Download schema.sql", data=bundle["schema.sql"], file_name="schema.sql", mime="text/sql", type="primary")
        st.caption("Use the copy icon in the SQL code block to copy the script. The script contains no data-loading statements and no database credentials.")
        st.divider()
        _title("Output B · Import preparation", "The dependency order, source-to-target mapping and known validation issues travel with the SQL.")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Recommended table order**")
            st.dataframe(pd.DataFrame(plan["data_load_order"]), use_container_width=True, hide_index=True)
            st.markdown("**Constraint strategy**")
            st.write(plan["constraint_order"])
        with col2:
            st.markdown("**Validation summary**")
            error_count, warning_count, info_count = _severity_summary(st.session_state.issues)
            st.write(f"Errors: **{error_count}** · Warnings: **{warning_count}** · Informational: **{info_count}**")
            st.markdown("**Import guidance**")
            st.write("Create tables first, resolve key/type issues, load parent datasets before child datasets, then verify row counts. See README.txt for psql \\copy versus server-side COPY guidance.")
        st.divider()
        _title("Output C · Downloadable reports", "Each output is generated from the current input and schema; none is a hard-coded demonstration.")
        download_specs = [
            ("schema_report.json", "Machine-readable schema and inference evidence", "application/json"),
            ("schema_report.csv", "Table/column summary and source mapping", "text/csv"),
            ("relationship_report.csv", "Candidates, confidence, overlap, unmatched references and approval state", "text/csv"),
            ("validation_report.csv", "Validation issues with source row locations", "text/csv"),
            ("source_mapping.csv", "Source file/worksheet/column → target table/column", "text/csv"),
            ("normalization_opportunities.csv", "Conservative repeated-value and shared-domain suggestions", "text/csv"),
            ("README.txt", "PostgreSQL execution and import instructions", "text/plain"),
            ("erd.svg", "Entity Relationship Diagram", "image/svg+xml"),
        ]
        grid = st.columns(3)
        for index, (filename, description, mime) in enumerate(download_specs):
            with grid[index % 3]:
                st.markdown(f"**{filename}**")
                st.caption(description)
                st.download_button(f"Download {filename}", data=bundle[filename], file_name=filename, mime=mime, key=f"download_{filename}", use_container_width=True)
        st.download_button("Download all outputs (.zip)", data=_zip_bundle(bundle), file_name="smartsql_outputs.zip", mime="application/zip", type="primary")
        with st.expander("Inspect generated schema report", expanded=False):
            report_obj = json.loads(bundle["schema_report.json"].decode("utf-8"))
            st.json({"target_dialect": report_obj["target_dialect"], "design_mode": report_obj["design_mode"], "tables": [{"name": table["name"], "column_count": sum(col.get("included", True) for col in table["columns"])} for table in report_obj["tables"]], "validation_summary": report_obj["validation_summary"], "limitations": report_obj["limitations"]})
    else:
        st.info("Generate the SQL and reports to see the final script and download links. If generation fails, the displayed error explains what needs to change.")
