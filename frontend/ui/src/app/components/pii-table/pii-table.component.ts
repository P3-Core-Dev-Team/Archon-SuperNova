import { Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { JobService } from '../../services/job.service';
import { PiiFinding } from '../../models/job.model';

@Component({
  selector: 'app-pii-table',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="toolbar">
      <input type="search" placeholder="Filter detector / PII type / column…"
             [ngModel]="filter()" (ngModelChange)="filter.set($event)"
             class="filter" />
      <label>
        <input type="checkbox" [checked]="onlyValidated()"
               (change)="onlyValidated.set(($any($event.target)).checked)" />
        Validated only
      </label>
      <span class="muted">{{ filtered().length }} of {{ total() }} findings</span>
    </div>

    @if (!loading() && !error() && total() === 0) {
      <div class="card muted center pad">No PII findings yet.</div>
    }

    @if (total() > 0) {
      <div class="card no-pad">
        <table>
          <thead>
            <tr>
              <th (click)="sortBy('table_name')">Table</th>
              <th (click)="sortBy('column_name')">Column</th>
              <th (click)="sortBy('pii_type')">PII Type</th>
              <th (click)="sortBy('detector')">Detector</th>
              <th class="r" (click)="sortBy('match_count')">Matches</th>
              <th class="r" (click)="sortBy('sample_count')">Samples</th>
              <th class="r" (click)="sortBy('match_rate')">Rate</th>
              <th class="r" (click)="sortBy('score')">Score</th>
              <th>Validated</th>
              <th>Name prior</th>
              <th>Examples (redacted)</th>
            </tr>
          </thead>
          <tbody>
            <tr *ngFor="let f of filtered()" [class]="'tier-' + tier(f)">
              <td class="mono">
                <a class="tlink" [routerLink]="['/jobs', jobId(), 'tables', f.table_name]"
                   title="Open the queryviz-style page for this table">{{ f.table_name }}</a>
              </td>
              <td class="mono">{{ f.column_name }}</td>
              <td>
                @if (isPci(f)) {
                  <!-- PCI cardholder data: render a single flat [PCI] chip
                       carrying Card Number / Card Name / CVV + brand
                       breakdown in the title tooltip.  No supplementary
                       per-type or per-brand chips. -->
                  <span class="reg-chip reg-pci" [title]="pciTooltip(f)">PCI</span>
                } @else {
                  @for (reg of regulationTags(f); track reg) {
                    <span class="reg-chip" [class]="'reg-' + reg.toLowerCase()"
                          [title]="reg + ' regulated data'">{{ reg }}</span>
                  }
                  <span class="pii-type" [title]="f.pii_type">{{ piiLabel(f.pii_type) }}</span>
                }
              </td>
              <td class="mono small">{{ f.detector }}</td>
              <td class="r mono">{{ f.match_count }}</td>
              <td class="r mono">{{ f.sample_count }}</td>
              <td class="r mono">{{ (f.match_rate * 100) | number:'1.1-1' }}%</td>
              <td class="r mono">{{ f.score == null ? '—' : (f.score | number:'1.2-2') }}</td>
              <td>
                <span *ngIf="f.validated" class="ok">✓</span>
                <span *ngIf="!f.validated" class="no">—</span>
              </td>
              <td>
                <span *ngIf="f.name_prior" class="ok">✓</span>
                <span *ngIf="!f.name_prior" class="no">—</span>
              </td>
              <td class="mono small ex" [attr.title]="exampleTooltip(f)">{{ exampleText(f) }}</td>
            </tr>
            <tr *ngIf="filtered().length === 0">
              <td colspan="11" class="muted center">No findings match the filter.</td>
            </tr>
          </tbody>
        </table>
      </div>
    }

    @if (loading()) { <div class="muted">Loading…</div> }
    @if (error()) { <div class="error">{{ error() }}</div> }
  `,
  styles: [`
    .toolbar {
      display: flex;
      gap: 16px;
      align-items: center;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .filter { min-width: 280px; }
    .toolbar label {
      display: inline-flex;
      gap: 6px;
      align-items: center;
      text-transform: none;
      letter-spacing: 0;
      font-size: 13px;
      color: #e6edf3;
      margin: 0;
    }
    .card.no-pad { padding: 0; overflow: auto; max-height: 640px; }
    .card.pad { padding: 24px; text-align: center; }
    th { cursor: pointer; user-select: none; }
    th.r, td.r { text-align: right; }
    .center { text-align: center; padding: 24px 0 !important; }
    .small { font-size: 12px; color: #8b949e; }
    /* Confidence-tier row tinting (uses left border + faint background) */
    tr.tier-high   td { background: rgba(218, 54, 51, 0.10); }
    tr.tier-high   td:first-child  { box-shadow: inset 3px 0 0 #f85149; }
    tr.tier-mid    td { background: rgba(210, 153, 34, 0.10); }
    tr.tier-mid    td:first-child  { box-shadow: inset 3px 0 0 #d29922; }
    tr.tier-low    td { background: rgba(139, 148, 158, 0.06); }
    tr.tier-low    td:first-child  { box-shadow: inset 3px 0 0 #6e7681; }
    tr:hover td { filter: brightness(1.15); }
    td.ex {
      max-width: 280px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      cursor: help;
    }
    .pii-type {
      background: #1f6feb;
      color: white;
      padding: 1px 8px;
      border-radius: 10px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.4px;
    }
    /* Regulation tags — small filled chip carrying the regulatory
       framework that gates this finding (PCI, GDPR, HIPAA, etc.).
       Drawn BEFORE the pii_type so a row scanning left-to-right
       reads "[PCI] Card Number" rather than vice-versa.  Same dark-
       on-tinted treatment as the brand chips. */
    .reg-chip {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 6px;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      margin-right: 4px;
      cursor: help;
      vertical-align: 1px;
    }
    .reg-chip.reg-pci   { color: #ff7b8b; background: rgba(235, 0, 27, 0.16); border: 1px solid rgba(235, 0, 27, 0.35); }
    .reg-chip.reg-gdpr  { color: #79c0ff; background: rgba(121, 192, 255, 0.16); border: 1px solid rgba(121, 192, 255, 0.35); }
    .reg-chip.reg-hipaa { color: #56d364; background: rgba(86, 211, 100, 0.16); border: 1px solid rgba(86, 211, 100, 0.35); }
    .reg-chip.reg-ccpa  { color: #d2a8ff; background: rgba(210, 168, 255, 0.16); border: 1px solid rgba(210, 168, 255, 0.35); }
    .reg-chip.reg-sox   { color: #d29922; background: rgba(210, 153, 34, 0.16); border: 1px solid rgba(210, 153, 34, 0.35); }
    .reg-chip.reg-dpdpa { color: #ffa05a; background: rgba(255, 96, 0, 0.16); border: 1px solid rgba(255, 96, 0, 0.35); }
    /* IIN/BIN provider chips — one per detected card scheme.  Tinted
       per brand to match the issuer's recognised colour, with a count
       badge tucked on the right.  Wraps below the PII tag if needed. */
    .brand-row {
      display: inline-flex;
      gap: 4px;
      margin-left: 6px;
      flex-wrap: wrap;
    }
    .brand-chip {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 1px 6px;
      border-radius: 8px;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      cursor: help;
      border: 1px solid transparent;
    }
    .brand-chip .brand-count {
      font-weight: 500;
      letter-spacing: 0;
      opacity: 0.8;
    }
    .brand-visa       { color: #1a1f71; background: rgba(26, 31, 113, 0.18); border-color: rgba(26, 31, 113, 0.35); color: #6f8aff; }
    .brand-mastercard { color: #eb001b; background: rgba(235, 0, 27, 0.16);  border-color: rgba(235, 0, 27, 0.35);  color: #ff7b8b; }
    .brand-amex       { color: #2e77bb; background: rgba(46, 119, 187, 0.18); border-color: rgba(46, 119, 187, 0.35); color: #7cb3e8; }
    .brand-discover   { color: #ff6000; background: rgba(255, 96, 0, 0.16);  border-color: rgba(255, 96, 0, 0.35);  color: #ffa05a; }
    .brand-diners     { color: #0079be; background: rgba(0, 121, 190, 0.18); border-color: rgba(0, 121, 190, 0.35); color: #66b2e0; }
    .brand-jcb        { color: #0e4c96; background: rgba(14, 76, 150, 0.18); border-color: rgba(14, 76, 150, 0.35); color: #739dd0; }
    .brand-unionpay   { color: #d10429; background: rgba(209, 4, 41, 0.16);  border-color: rgba(209, 4, 41, 0.35);  color: #ff8090; }
    .brand-maestro    { color: #6c6bbd; background: rgba(108, 107, 189, 0.18); border-color: rgba(108, 107, 189, 0.35); color: #a8a7ee; }
    .brand-rupay      { color: #097969; background: rgba(9, 121, 105, 0.18); border-color: rgba(9, 121, 105, 0.35); color: #4cb6a6; }
    .brand-mir        { color: #4db45e; background: rgba(77, 180, 94, 0.18); border-color: rgba(77, 180, 94, 0.35); color: #88e09a; }
    .ok { color: #3fb950; font-weight: 700; }
    .no { color: #6e7681; }
    .error {
      color: #ffabab;
      padding: 12px;
      background: #3a0d0d;
      border: 1px solid #f85149;
      border-radius: 6px;
    }
  `],
})
export class PiiTableComponent implements OnInit {
  jobId = input.required<string>();

  private jobsSvc = inject(JobService);

  findings = signal<PiiFinding[]>([]);
  total = signal(0);
  loading = signal(true);
  error = signal<string | null>(null);

  filter = signal('');
  onlyValidated = signal(false);
  // Per spec: default sort by confidence (score) DESC.
  sortKey = signal<keyof PiiFinding>('score');
  sortAsc = signal(false);

  filtered = computed(() => {
    const q = this.filter().trim().toLowerCase();
    const onlyVal = this.onlyValidated();
    const key = this.sortKey();
    const asc = this.sortAsc();
    let rows = this.findings();
    if (q) {
      rows = rows.filter(f =>
        f.detector.toLowerCase().includes(q) ||
        f.pii_type.toLowerCase().includes(q) ||
        f.table_name.toLowerCase().includes(q) ||
        f.column_name.toLowerCase().includes(q),
      );
    }
    if (onlyVal) {
      rows = rows.filter(f => f.validated);
    }
    return [...rows].sort((a, b) => {
      const av = a[key]; const bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return asc ? -1 : 1;
      if (av > bv) return asc ? 1 : -1;
      return 0;
    });
  });

  ngOnInit(): void {
    this.jobsSvc.pii(this.jobId()).subscribe({
      next: r => {
        this.findings.set(r.findings);
        this.total.set(r.total);
        this.loading.set(false);
      },
      error: err => {
        this.loading.set(false);
        this.error.set(
          err?.error?.detail ?? err?.message ?? 'Failed to load PII findings.',
        );
      },
    });
  }

  sortBy(key: keyof PiiFinding): void {
    if (this.sortKey() === key) {
      this.sortAsc.set(!this.sortAsc());
    } else {
      this.sortKey.set(key);
      this.sortAsc.set(false);
    }
  }

  /** Confidence tier for row tinting. */
  tier(f: PiiFinding): 'high' | 'mid' | 'low' {
    const s = f.score;
    if (s != null && s > 0.95) return 'high';
    if (s != null && s >= 0.85) return 'mid';
    return 'low';
  }

  exampleText(f: PiiFinding): string {
    if (!Array.isArray(f.redacted_examples) || f.redacted_examples.length === 0) {
      return '—';
    }
    return f.redacted_examples.slice(0, 3).map(String).join(', ');
  }

  exampleTooltip(f: PiiFinding): string {
    if (!Array.isArray(f.redacted_examples) || f.redacted_examples.length === 0) {
      return '';
    }
    return f.redacted_examples.map(String).join('\n');
  }

  /** Friendly display label for a PII type — shorter, more readable
   * than the raw catalog name.  The full ``pii_type`` symbol stays in
   * the title attribute for users who need the original string. */
  private static readonly _PII_LABEL_MAP: Record<string, string> = {
    CC_NUMBER: 'Card Number',
    CARD_HOLDER_NAME: 'Card Name',
    CARD_CVV: 'CVV',
    CREDENTIAL_HASH: 'Credential Hash',
    SSN_US: 'SSN (US)',
    PHONE_US: 'Phone (US)',
    PASSPORT_US: 'Passport (US)',
    PASSPORT_GB: 'Passport (UK)',
    PASSPORT_IN: 'Passport (IN)',
    AADHAAR_IN: 'Aadhaar',
    PAN_IN: 'PAN (IN)',
    NHS_GB: 'NHS Number',
    NIR_FR: 'NIR',
    PERSON_NAME: 'Person Name',
    EMAIL: 'Email',
    POSTAL_CODE: 'Postal Code',
    COUNTRY_CODE: 'Country Code',
    ADDRESS: 'Address',
    DOB: 'Date of Birth',
    IBAN: 'IBAN',
    SWIFT_BIC: 'SWIFT / BIC',
    BANK_ACCOUNT: 'Bank Account',
    ABA_ROUTING_US: 'ABA Routing',
  };

  piiLabel(piiType: string): string {
    return PiiTableComponent._PII_LABEL_MAP[piiType] ?? piiType;
  }

  /** Regulation-tag chips to render before the PII type.  Returns the
   * ``regulated`` array as-is; UI deduplication is implicit (the
   * backend already returns each tag once per finding). */
  regulationTags(f: PiiFinding): string[] {
    return Array.isArray(f.regulated) ? f.regulated : [];
  }

  /** True iff this finding is in the PCI cardholder-data group.
   * Used to collapse the row's chip stack down to a single flat
   * ``[PCI]`` chip; the detail (Card Number / Card Name / CVV +
   * brand breakdown) goes into the tooltip. */
  isPci(f: PiiFinding): boolean {
    return Array.isArray(f.regulated) && f.regulated.includes('PCI');
  }

  /** Compose the ``[PCI]`` chip's title tooltip.  Carries the
   * underlying type label (Card Number / Card Name / CVV) and, when
   * present, the IIN/BIN brand breakdown. */
  pciTooltip(f: PiiFinding): string {
    const parts: string[] = [`PCI: ${this.piiLabel(f.pii_type)}`];
    if (Array.isArray(f.provider_breakdown) && f.provider_breakdown.length > 0) {
      parts.push(
        f.provider_breakdown
          .map(p => `${p.brand}: ${p.count}`)
          .join(', '),
      );
    }
    if (Array.isArray(f.redacted_examples) && f.redacted_examples.length > 0) {
      parts.push('e.g. ' + f.redacted_examples.slice(0, 3).map(String).join(', '));
    }
    return parts.join('\n');
  }
}
