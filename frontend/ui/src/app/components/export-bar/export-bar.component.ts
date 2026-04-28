import { Component, inject, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { catchError, forkJoin, of } from 'rxjs';
import { JobService } from '../../services/job.service';
import {
  ColumnInfo, JobColumns, RelationshipEdge, RelationshipGraph,
} from '../../models/job.model';

/**
 * Export bar.
 *
 * Three buttons that fetch /api/jobs/{id}/relationships (and optionally
 * /columns) and write a downloadable artifact in DBML, Mermaid ER, or JSON.
 *
 * If the optional /columns endpoint (added by agent B2) is available, exports
 * include real data types and the full column inventory. If it returns 404
 * (older API process), exports degrade gracefully: columns are inferred from
 * edge labels and all data types fall back to `text`. The first line of
 * DBML/Mermaid output documents which mode produced the file.
 *
 * Other limitations:
 *   - In fallback mode, only tables that appear in `nodes` are emitted; orphan
 *     tables (no FKs) are omitted by the relationships endpoint itself.
 *   - Edge column information is parsed from `edge.label` ("child_col → parent_col");
 *     if the format changes, exports degrade but won't crash.
 */
@Component({
  selector: 'app-export-bar',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="bar">
      <button type="button" (click)="export('dbml')" [disabled]="busy()">Export DBML</button>
      <button type="button" (click)="export('mermaid')" [disabled]="busy()">Export Mermaid</button>
      <button type="button" (click)="export('json')" [disabled]="busy()">Export JSON</button>
      @if (error()) {
        <span class="err small">{{ error() }}</span>
      }
    </div>
  `,
  styles: [`
    .bar {
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }
    .bar button {
      padding: 6px 10px;
      font-size: 12px;
    }
    .err {
      color: #cf222e;
      margin-left: 8px;
    }
    .small { font-size: 12px; }
  `],
})
export class ExportBarComponent {
  jobId = input.required<string>();

  private jobsSvc = inject(JobService);

  busy = signal(false);
  error = signal<string | null>(null);

  export(fmt: 'dbml' | 'mermaid' | 'json'): void {
    this.busy.set(true);
    this.error.set(null);
    // Fetch relationships always; columns is optional — fall back to null on 404 etc.
    forkJoin({
      g: this.jobsSvc.relationships(this.jobId(), 1000),
      cols: this.jobsSvc.columns(this.jobId()).pipe(
        catchError(() => of<JobColumns | null>(null)),
      ),
    }).subscribe({
      next: ({ g, cols }) => {
        try {
          const text = fmt === 'dbml' ? toDbml(g, cols)
            : fmt === 'mermaid' ? toMermaid(g, cols)
            : toJson(g, cols, this.jobId());
          const ext = fmt === 'mermaid' ? 'mmd' : fmt;
          const fname = `discovery-${safeFile(g.schema || 'schema')}.${ext}`;
          download(fname, text, fmt === 'json' ? 'application/json' : 'text/plain');
        } catch (e: any) {
          this.error.set(e?.message ?? 'Export failed.');
        }
        this.busy.set(false);
      },
      error: err => {
        this.busy.set(false);
        this.error.set(err?.error?.detail ?? err?.message ?? 'Failed to fetch relationships.');
      },
    });
  }
}

/* ------------------------------- helpers ------------------------------- */

interface ParsedEdge {
  childTable: string;
  childCol: string;
  parentTable: string;
  parentCol: string;
  cardinality: string;
  confidence: number | null;
}

function parseEdge(e: RelationshipEdge): ParsedEdge {
  const sep = e.label.includes('→') ? '→' : '->';
  const [c, p] = e.label.split(sep).map(s => s.trim());
  return {
    childTable: e.from,
    childCol: c || 'unknown_col',
    parentTable: e.to,
    parentCol: p || 'unknown_col',
    cardinality: e.cardinality,
    confidence: e.confidence,
  };
}

interface ColEntry {
  name: string;
  type: string;
  isPk: boolean;
}

/**
 * Collect columns per table.
 *  - If `cols` (from /columns endpoint) is provided, use it for full column
 *    inventory + real data types.
 *  - Otherwise, infer from edge labels: PKs are parent_col targets, FKs are
 *    child_col sources, all types fall back to 'text'.
 *
 * Returns insertion-ordered Map keyed by table name.
 */
function collectColumns(
  g: RelationshipGraph,
  cols: JobColumns | null,
): Map<string, ColEntry[]> {
  const out = new Map<string, ColEntry[]>();
  if (cols && cols.columns?.length) {
    // Group by table, preserve ordinal order.
    const grouped = new Map<string, ColumnInfo[]>();
    for (const c of cols.columns) {
      let arr = grouped.get(c.table);
      if (!arr) { arr = []; grouped.set(c.table, arr); }
      arr.push(c);
    }
    const tableNames = [...new Set([...(cols.tables ?? []), ...grouped.keys()])].sort();
    for (const t of tableNames) {
      const arr = (grouped.get(t) ?? [])
        .slice()
        .sort((a, b) => a.ordinal - b.ordinal);
      out.set(t, arr.map(c => ({
        name: c.column,
        type: c.data_type || 'text',
        isPk: c.is_pk,
      })));
    }
    return out;
  }
  // Fallback: infer from edges.
  const inferred = new Map<string, Map<string, boolean>>();
  const ensure = (t: string) => {
    let m = inferred.get(t);
    if (!m) { m = new Map(); inferred.set(t, m); }
    return m;
  };
  for (const n of g.nodes) ensure(n.label);
  for (const e of g.edges) {
    const p = parseEdge(e);
    const child = ensure(p.childTable);
    if (!child.has(p.childCol)) child.set(p.childCol, false);
    const parent = ensure(p.parentTable);
    // Mark parent col as PK (the referenced column of an FK is the primary key).
    parent.set(p.parentCol, true);
  }
  const tableNames = [...inferred.keys()].sort();
  for (const t of tableNames) {
    const m = inferred.get(t)!;
    out.set(t, [...m.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([name, isPk]) => ({ name, type: 'text', isPk })),
    );
  }
  return out;
}

function toDbml(g: RelationshipGraph, jc: JobColumns | null): string {
  const cols = collectColumns(g, jc);
  const lines: string[] = [];
  lines.push(`// Archon-SuperNova export — schema "${g.schema}"`);
  if (jc) {
    lines.push(`// Columns and data types from /api/jobs/{id}/columns.`);
  } else {
    lines.push(`// Columns inferred from FK relationships only; column types default to 'text'.`);
    lines.push(`// (Optional /columns endpoint unavailable.)`);
  }
  lines.push('');
  for (const [t, list] of cols.entries()) {
    lines.push(`Table ${dbmlIdent(t)} {`);
    if (list.length === 0) {
      lines.push(`  // no columns`);
    }
    for (const c of list) {
      const tag = c.isPk ? ' [pk]' : '';
      lines.push(`  ${dbmlIdent(c.name)} ${dbmlType(c.type)}${tag}`);
    }
    lines.push('}');
    lines.push('');
  }
  for (const e of g.edges) {
    const p = parseEdge(e);
    lines.push(
      `Ref: ${dbmlIdent(p.childTable)}.${dbmlIdent(p.childCol)} > ` +
      `${dbmlIdent(p.parentTable)}.${dbmlIdent(p.parentCol)}`,
    );
  }
  return lines.join('\n') + '\n';
}

function toMermaid(g: RelationshipGraph, jc: JobColumns | null): string {
  const cols = collectColumns(g, jc);
  const lines: string[] = [];
  lines.push(`%% Archon-SuperNova export — schema "${g.schema}"`);
  if (jc) {
    lines.push(`%% Columns and data types from /api/jobs/{id}/columns.`);
  } else {
    lines.push(`%% Columns inferred from FK relationships only; column types default to 'text'.`);
  }
  lines.push('erDiagram');
  for (const e of g.edges) {
    const p = parseEdge(e);
    const card = mermaidCard(p.cardinality);
    lines.push(`  ${mmIdent(p.parentTable)} ${card} ${mmIdent(p.childTable)} : "${escMm(p.childCol)}"`);
  }
  for (const [t, list] of cols.entries()) {
    lines.push(`  ${mmIdent(t)} {`);
    if (list.length === 0) {
      lines.push(`    text _placeholder`);
    }
    for (const c of list) {
      const tag = c.isPk ? ' PK' : '';
      lines.push(`    ${mmType(c.type)} ${mmIdent(c.name).toLowerCase()}${tag}`);
    }
    lines.push(`  }`);
  }
  return lines.join('\n') + '\n';
}

function toJson(
  g: RelationshipGraph,
  jc: JobColumns | null,
  jobId: string,
): string {
  return JSON.stringify(
    {
      job_id: jobId,
      generated_at: new Date().toISOString(),
      schema: g.schema,
      total_tables: g.total_tables,
      total_edges: g.total_edges,
      nodes: g.nodes,
      edges: g.edges,
      columns: jc?.columns ?? null,
    },
    null,
    2,
  ) + '\n';
}

/** DBML accepts most SQL types; pass through, fall back to text. */
function dbmlType(t: string): string {
  const v = (t || '').trim();
  if (!v) return 'text';
  // Quote types containing whitespace or non-word chars to keep dbdiagram.io happy.
  return /^[A-Za-z][A-Za-z0-9_]*$/.test(v) ? v : `"${v.replace(/"/g, '\\"')}"`;
}

/** Mermaid attribute types must be word chars only; sanitize. */
function mmType(t: string): string {
  const cleaned = (t || 'text').replace(/[^A-Za-z0-9_]/g, '_');
  return cleaned || 'text';
}

/** Map our cardinality strings (e.g., "many-to-one") to Mermaid notation. */
function mermaidCard(card: string): string {
  // parent ?--? child
  // many-to-one  → parent has many children: "||--o{"
  // one-to-one   → "||--||"
  // one-to-many  → (rare in our data; same as many-to-one from parent perspective)
  // unknown      → default to one-to-many ("||--o{")
  switch ((card || '').toLowerCase()) {
    case 'one-to-one':  return '||--||';
    case 'many-to-one': return '||--o{';
    case 'one-to-many': return '||--o{';
    case 'many-to-many':return '}o--o{';
    default:            return '||--o{';
  }
}

const IDENT_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** DBML identifiers: quote with double-quotes if non-word chars present. */
function dbmlIdent(name: string): string {
  return IDENT_RE.test(name) ? name : `"${name.replace(/"/g, '\\"')}"`;
}

/** Mermaid ER entity/attribute names: must be word chars. Uppercase tables, leave attrs as-is. */
function mmIdent(name: string): string {
  // Replace any non-word character with underscore; ensure we don't return an empty string.
  const cleaned = name.replace(/[^A-Za-z0-9_]/g, '_');
  return cleaned || '_';
}

function escMm(s: string): string {
  return s.replace(/"/g, '\\"');
}

function safeFile(s: string): string {
  return s.replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 80) || 'export';
}

function download(filename: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Defer revocation a tick so the browser can start the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
