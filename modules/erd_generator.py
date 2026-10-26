"""Self-contained SVG ER diagram generator (no external Graphviz binary required)."""
from __future__ import annotations

from html import escape
from typing import Any


def generate_erd_svg(tables: list[dict[str, Any]], relationships: list[dict[str, Any]], *, width: int = 1200) -> str:
    display_tables = [table for table in tables if any(col.get("included", True) for col in table["columns"])]
    if not display_tables:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="160"><rect width="100%" height="100%" fill="#f8fafc"/><text x="24" y="80" font-family="sans-serif" font-size="18" fill="#475569">No tables selected.</text></svg>'
    card_w = 250
    row_h = 25
    title_h = 38
    max_columns = max(len([c for c in t["columns"] if c.get("included", True)]) for t in display_tables)
    card_h = title_h + max(1, max_columns) * row_h + 16
    gap_x, gap_y, pad = 70, 70, 40
    cols_per_row = max(1, min(4, (width - 2 * pad + gap_x) // (card_w + gap_x)))
    rows = (len(display_tables) + cols_per_row - 1) // cols_per_row
    height = pad * 2 + rows * card_h + max(0, rows - 1) * gap_y
    positions: dict[str, tuple[int, int]] = {}
    for i, table in enumerate(display_tables):
        row, column = divmod(i, cols_per_row)
        positions[table["id"]] = (pad + column * (card_w + gap_x), pad + row * (card_h + gap_y))
    edges: list[str] = []
    for rel in relationships:
        if rel.get("child_table_id") not in positions or rel.get("parent_table_id") not in positions:
            continue
        cx, cy = positions[rel["child_table_id"]]
        px, py = positions[rel["parent_table_id"]]
        sx, sy = cx + card_w // 2, cy
        tx, ty = px + card_w // 2, py + card_h
        if abs(cy - py) < 30:
            sy, ty = cy + card_h // 2, py + card_h // 2
            sx, tx = cx + (card_w if px > cx else 0), px + (0 if px > cx else card_w)
        mid_y = (sy + ty) // 2
        valid = rel.get("valid_for_fk", False)
        color = "#159a80" if valid and rel.get("approved") else "#c58a26" if valid else "#c45f67"
        dash = "" if rel.get("approved") else ' stroke-dasharray="6 5"'
        edges.append(f'<path d="M {sx} {sy} Q {sx} {mid_y} {(sx+tx)//2} {mid_y} T {tx} {ty}" fill="none" stroke="{color}" stroke-width="2"{dash}/>'
                     f'<circle cx="{tx}" cy="{ty}" r="4" fill="{color}"/>')
    cards: list[str] = []
    for table in display_tables:
        x, y = positions[table["id"]]
        active_cols = [c for c in table["columns"] if c.get("included", True)]
        cards.append(f'<g><rect x="{x}" y="{y}" width="{card_w}" height="{title_h + max(1, len(active_cols))*row_h + 16}" rx="12" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.5"/>')
        cards.append(f'<path d="M {x+12} {y} H {x+card_w-12} Q {x+card_w} {y} {x+card_w} {y+12} V {y+title_h} H {x} V {y+12} Q {x} {y} {x+12} {y}" fill="#142d4e"/>')
        cards.append(f'<text x="{x+14}" y="{y+25}" font-family="Inter,Arial,sans-serif" font-size="14" font-weight="700" fill="#ffffff">{escape(table["name"][:28])}</text>')
        for index, col in enumerate(active_cols):
            ty = y + title_h + 18 + index * row_h
            badge = "PK" if col.get("primary_key") else "UQ" if col.get("unique") else "FK" if any(rel.get("child_table_id") == table["id"] and rel.get("child_column_source") == col["source_name"] for rel in relationships) else ""
            badge_color = "#126b5b" if badge in ("PK", "FK") else "#a46c12"
            cards.append(f'<text x="{x+14}" y="{ty}" font-family="Inter,Arial,sans-serif" font-size="11.5" fill="#334155">{escape(col["name"][:23])}</text>')
            cards.append(f'<text x="{x+card_w-12}" y="{ty}" text-anchor="end" font-family="Inter,Arial,sans-serif" font-size="10.5" fill="#64748b">{escape(col["sql_type"][:14])}</text>')
            if badge:
                cards.append(f'<rect x="{x+card_w-51}" y="{ty-11}" width="21" height="15" rx="4" fill="{badge_color}"/>')
                cards.append(f'<text x="{x+card_w-40.5}" y="{ty}" text-anchor="middle" font-family="Arial,sans-serif" font-size="8.5" font-weight="700" fill="#ffffff">{badge}</text>')
        cards.append('</g>')
    legend_y = height - 18
    legend = (f'<g font-family="Inter,Arial,sans-serif" font-size="11" fill="#475569">'
              f'<circle cx="{pad+5}" cy="{legend_y-4}" r="4" fill="#159a80"/><text x="{pad+15}" y="{legend_y}">Approved / valid</text>'
              f'<circle cx="{pad+135}" cy="{legend_y-4}" r="4" fill="#c58a26"/><text x="{pad+145}" y="{legend_y}">Candidate / review</text>'
              f'<circle cx="{pad+285}" cy="{legend_y-4}" r="4" fill="#c45f67"/><text x="{pad+295}" y="{legend_y}">Unmatched references</text></g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="100%" height="100%" fill="#f8fafc"/>' + ''.join(edges) + ''.join(cards) + legend + '</svg>')
