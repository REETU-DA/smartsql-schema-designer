"""Safe, in-memory readers for tabular source files.

Source values are deliberately read as objects/strings wherever practical so
identifiers such as 00125 are not silently converted to integers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO, StringIO
from pathlib import PurePosixPath
import csv
import json
import re
import zipfile
from typing import Any

import pandas as pd

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MiB per uploaded file
SUPPORTED_SUFFIXES = {
    ".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm", ".json", ".jsonl",
    ".ndjson", ".parquet", ".xml", ".zip",
}


@dataclass
class Dataset:
    """A parsed table with source lineage and parse diagnostics."""

    dataset_id: str
    source_file: str
    source_sheet: str | None
    file_format: str
    frame: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    source_record_offset: int = 2
    size_bytes: int = 0

    @property
    def label(self) -> str:
        if self.source_sheet:
            return f"{self.source_file} · {self.source_sheet}"
        return self.source_file


def _dedupe_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Give duplicate/blank headers stable unique names and report changes."""
    used: set[str] = set()
    new_columns: list[str] = []
    warnings: list[str] = []
    for position, raw in enumerate(frame.columns, start=1):
        original = "" if raw is None else str(raw).strip()
        base = original or f"column_{position}"
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        if candidate != original:
            if original:
                warnings.append(f"Duplicate column name {original!r} was renamed to {candidate!r} to keep columns addressable.")
            else:
                warnings.append(f"Blank column header at position {position} was named {candidate!r}.")
        used.add(candidate.casefold())
        new_columns.append(candidate)
    result = frame.copy()
    result.columns = new_columns
    return result, warnings


def _normalise_frame(frame: pd.DataFrame, name: str) -> tuple[pd.DataFrame, list[str]]:
    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame(frame)
    frame = frame.copy()
    frame, warnings = _dedupe_columns(frame)
    complex_columns = []
    for column in frame.columns:
        has_complex = frame[column].map(lambda value: isinstance(value, (dict, list, tuple, set))).any()
        if has_complex:
            complex_columns.append(str(column))
            frame[column] = frame[column].map(lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if isinstance(value, (dict, list, tuple, set)) else value)
    if complex_columns:
        warnings.append("Nested object/array values were serialised to JSON text in: " + ", ".join(complex_columns) + ".")
    if len(frame.columns) == 0:
        warnings.append("The dataset contains no columns.")
    if len(frame) == 0:
        warnings.append("The dataset is empty or contains only a header row.")
    # Keep source values stable, but make pandas' NaN representations consistent.
    return frame.reset_index(drop=True), warnings


def _looks_like_data_row(values: list[Any]) -> bool:
    """Heuristic warning only; header mode is controlled explicitly by the user."""
    if len(values) < 2:
        return False
    patterns = [
        re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$"),
        re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$"),
        re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
        re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.I),
    ]
    values = ["" if value is None else str(value).strip() for value in values]
    if values and any(pattern.fullmatch(values[0]) for pattern in patterns):
        return True
    nonempty = [value for value in values if value]
    data_like = sum(any(pattern.fullmatch(value) for pattern in patterns) for value in nonempty)
    return bool(nonempty) and data_like / len(nonempty) >= 0.75


def _parse_delimited(content: bytes, suffix: str, *, has_header: bool = True) -> tuple[pd.DataFrame, list[str], int]:
    warnings: list[str] = []
    text: str | None = None
    encoding_used = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
            encoding_used = encoding
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("The file encoding could not be detected. Save it as UTF-8 and try again.")
    if encoding_used not in ("utf-8-sig", "utf-8"):
        warnings.append(f"Decoded using {encoding_used}; verify non-ASCII text in the preview.")
    if suffix == ".tsv":
        sep: str = "\t"
    else:
        try:
            sample = text[:65536]
            sep = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except csv.Error:
            sep = ","
            warnings.append("Could not confidently detect the delimiter; comma was used. Confirm the preview.")
    try:
        # Parse the first row as data, then assign it ourselves. Pandas otherwise
        # silently rewrites duplicate headers (for example id -> id.1), making
        # it impossible to explain the source-name change accurately.
        raw_frame = pd.read_csv(StringIO(text), sep=sep, dtype=object, keep_default_na=False,
                                on_bad_lines="error", header=None)
    except Exception as exc:
        raise ValueError(f"Could not parse the delimited file: {exc}") from exc
    if raw_frame.empty or not len(raw_frame.columns):
        raise ValueError("No rows or columns could be detected.")
    if has_header:
        raw_headers = raw_frame.iloc[0].tolist()
        frame = raw_frame.iloc[1:].reset_index(drop=True)
        frame.columns = raw_headers
        if _looks_like_data_row(raw_headers):
            warnings.append("The first row looks like data rather than column names. If this file has no header row, re-analyze with the header option turned off.")
        offset = 2
    else:
        frame = raw_frame.reset_index(drop=True)
        frame.columns = [f"column_{i}" for i in range(1, len(frame.columns) + 1)]
        warnings.append("No header row was used; generic column names were assigned and all source rows were preserved.")
        offset = 1
    return frame, warnings, offset


def _parse_json(content: bytes, suffix: str) -> pd.DataFrame:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("JSON files must be UTF-8 encoded.") from exc
    if suffix in (".jsonl", ".ndjson"):
        rows: list[Any] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON Lines content on line {line_no}: {exc.msg}") from exc
        data: Any = rows
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).") from exc
    if isinstance(data, list):
        if data and all(isinstance(item, dict) for item in data):
            return pd.json_normalize(data, sep="__")
        return pd.DataFrame({"value": data})
    if isinstance(data, dict):
        # A dict of arrays or records becomes multiple columns; nested objects flatten.
        if data and all(isinstance(value, list) for value in data.values()):
            try:
                return pd.DataFrame(data)
            except ValueError:
                return pd.json_normalize(data, sep="__")
        return pd.json_normalize([data], sep="__")
    return pd.DataFrame({"value": [data]})


def _read_one(filename: str, content: bytes, *, has_header: bool = True) -> list[tuple[str | None, str, pd.DataFrame, list[str], int]]:
    suffix = PurePosixPath(filename).suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported file type {suffix or '(no extension)'}. Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB per-file limit.")

    if suffix in (".csv", ".tsv", ".txt"):
        frame, warnings, offset = _parse_delimited(content, suffix, has_header=has_header)
        return [(None, suffix[1:].upper(), frame, warnings, offset)]

    if suffix in (".xlsx", ".xlsm", ".xls"):
        try:
            # openpyxl reads workbook data only; macros are not executed.
            engine = "openpyxl" if suffix in (".xlsx", ".xlsm") else "xlrd"
            workbook = pd.read_excel(BytesIO(content), sheet_name=None, dtype=object, engine=engine, header=None)
        except ImportError as exc:
            dependency = "xlrd" if suffix == ".xls" else "openpyxl"
            raise ValueError(f"Reading this workbook requires the optional dependency {dependency}. Install project requirements and retry.") from exc
        except Exception as exc:
            raise ValueError(f"Could not read the Excel workbook: {exc}") from exc
        if not workbook:
            return [(None, "EXCEL", pd.DataFrame(), ["Workbook contains no worksheets."], 2)]
        result = []
        for sheet_name, raw_frame in workbook.items():
            parser_warnings: list[str] = []
            frame = raw_frame.copy()
            offset = 2 if has_header else 1
            if frame.empty:
                # Preserve an empty worksheet as a dataset so the user sees the issue.
                frame = pd.DataFrame()
            elif has_header:
                headers = frame.iloc[0].tolist()
                frame = frame.iloc[1:].reset_index(drop=True)
                frame.columns = headers
                if _looks_like_data_row(headers):
                    parser_warnings.append("The first worksheet row looks like data rather than column names. Re-analyze with the header option turned off if appropriate.")
            else:
                frame = frame.reset_index(drop=True)
                frame.columns = [f"column_{i}" for i in range(1, len(frame.columns) + 1)]
                parser_warnings.append("No header row was used; generic column names were assigned and all worksheet rows were preserved.")
            result.append((str(sheet_name), suffix[1:].upper(), frame, parser_warnings, offset))
        return result

    if suffix in (".json", ".jsonl", ".ndjson"):
        frame = _parse_json(content, suffix)
        return [(None, suffix[1:].upper(), frame, [], 1)]

    if suffix == ".parquet":
        try:
            frame = pd.read_parquet(BytesIO(content))
        except ImportError as exc:
            raise ValueError("Parquet support requires pyarrow or fastparquet. Install the project requirements and retry.") from exc
        except Exception as exc:
            raise ValueError(f"Could not read the Parquet file: {exc}") from exc
        return [(None, "PARQUET", frame, [], 1)]

    if suffix == ".xml":
        try:
            frame = pd.read_xml(BytesIO(content))
        except Exception as exc:
            raise ValueError(f"Could not interpret this XML as a repeating tabular structure: {exc}") from exc
        return [(None, "XML", frame, [], 1)]

    if suffix == ".zip":
        result: list[tuple[str | None, str, pd.DataFrame, list[str], int]] = []
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                members = [m for m in archive.infolist() if not m.is_dir() and PurePosixPath(m.filename).suffix.casefold() in SUPPORTED_SUFFIXES - {".zip"}]
                if not members:
                    raise ValueError("The ZIP archive contains no supported data files.")
                if len(members) > 50:
                    raise ValueError("ZIP archives are limited to 50 supported data files.")
                for member in members:
                    if member.file_size > MAX_UPLOAD_BYTES:
                        result.append((member.filename, "ZIP MEMBER", pd.DataFrame(), ["Member skipped: it exceeds the per-file limit."], 1))
                        continue
                    with archive.open(member) as stream:
                        member_bytes = stream.read(MAX_UPLOAD_BYTES + 1)
                    member_name = PurePosixPath(member.filename).name
                    # Nested archives are deliberately not recursively expanded.
                    for sheet, fmt, frame, warns, offset in _read_one(member_name, member_bytes, has_header=has_header):
                        result.append((member.filename + (f"::{sheet}" if sheet else ""), fmt, frame, warns, offset))
        except zipfile.BadZipFile as exc:
            raise ValueError("The ZIP archive is damaged or not a valid ZIP file.") from exc
        return result

    raise ValueError(f"No parser is available for {suffix}.")


def read_uploaded_file(filename: str, content: bytes, *, max_bytes: int = MAX_UPLOAD_BYTES, has_header: bool = True) -> list[Dataset]:
    """Parse one uploaded file into one or more datasets without writing it to disk."""
    if not isinstance(content, (bytes, bytearray)):
        raise TypeError("content must be bytes from an uploaded file")
    if len(content) > max_bytes:
        raise ValueError(f"File exceeds the configured {max_bytes // (1024 * 1024)} MiB limit.")
    source_parts = _read_one(filename, bytes(content), has_header=has_header)
    datasets: list[Dataset] = []
    for index, (sheet, fmt, raw_frame, parser_warnings, offset) in enumerate(source_parts, start=1):
        frame, normalise_warnings = _normalise_frame(raw_frame, filename)
        label = f"{filename}::{sheet}" if sheet else filename
        # Stable within one analysis run; collisions are resolved by schema designer.
        key = re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_").lower() or f"dataset_{index}"
        if len(source_parts) > 1:
            key = f"{key}_{index}"
        datasets.append(Dataset(
            dataset_id=key,
            source_file=filename,
            source_sheet=sheet,
            file_format=fmt,
            frame=frame,
            warnings=list(parser_warnings) + normalise_warnings,
            source_record_offset=offset,
            size_bytes=len(content),
        ))
    return datasets
