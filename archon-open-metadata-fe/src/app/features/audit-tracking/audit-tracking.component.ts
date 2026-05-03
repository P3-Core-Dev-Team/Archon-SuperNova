import { Component } from '@angular/core';

@Component({
  selector: 'app-audit-tracking',
  templateUrl: './audit-tracking.component.html',
  styleUrls: ['./audit-tracking.component.css']
})
export class AuditTrackingComponent {
  activeFilter = 'all';
  searchQuery = '';
  openRowIndex = -1;

  cols = [
    { tbl: 'KNA1', col: 'STCD1', cat: 'PII', det: 'Presidio · tax ID regex', conf: 0.99, risk: 'HIGH', review: false, desc: 'Customer tax number — national ID pattern detected', method: 'Regex + Luhn variant' },
    { tbl: 'PA0002', col: 'PERID', cat: 'PII', det: 'Presidio · national ID', conf: 0.98, risk: 'HIGH', review: false, desc: 'Employee personal identity number', method: 'Regex pattern match' },
    { tbl: 'PA0006', col: 'STRAS', cat: 'PII', det: 'spaCy · address NER', conf: 0.96, risk: 'HIGH', review: false, desc: 'Employee street address — location entity recognised', method: 'NER (GPE/LOC)' },
    { tbl: 'BSEG', col: 'CCARD', cat: 'PCI', det: 'Presidio · PAN pattern', conf: 0.99, risk: 'HIGH', review: false, desc: 'Primary Account Number detected', method: 'Credit Card Regex' },
    { tbl: 'KNBK', col: 'BANKN', cat: 'FIN', det: 'Presidio · IBAN pattern', conf: 0.97, risk: 'HIGH', review: false, desc: 'Customer bank account number — IBAN detected', method: 'IbanRecognizer' },
    { tbl: 'sap_hr', col: 'bank_acct', cat: 'FIN', det: 'spaCy · low confidence', conf: 0.53, risk: 'MED', review: true, desc: 'Possible bank account ref — below threshold', method: 'Text classification' },
    { tbl: 'PA0002', col: 'KRZKK', cat: 'PHI', det: 'spaCy · medical NER', conf: 0.87, risk: 'HIGH', review: false, desc: 'Employee health insurance indicator', method: 'NER (MEDICAL)' }
  ];

  get filteredCols() {
    const q = (this.searchQuery || '').toLowerCase();
    return this.cols.filter(c => {
      const txt = (c.tbl + c.col + c.cat + c.det).toLowerCase();
      if (q && !txt.includes(q)) return false;
      if (this.activeFilter === 'PII') return c.cat === 'PII';
      if (this.activeFilter === 'PCI') return c.cat === 'PCI';
      if (this.activeFilter === 'FIN') return c.cat === 'FIN';
      if (this.activeFilter === 'PHI') return c.cat === 'PHI';
      if (this.activeFilter === 'HIGH') return c.risk === 'HIGH';
      if (this.activeFilter === 'REVIEW') return c.review;
      return true;
    });
  }

  get stats() {
    const s = { total: this.cols.length, pii: 0, pci: 0, fin: 0, phi: 0 };
    this.cols.forEach(c => {
      if (c.cat === 'PII') s.pii++;
      if (c.cat === 'PCI') s.pci++;
      if (c.cat === 'FIN') s.fin++;
      if (c.cat === 'PHI') s.phi++;
    });
    return s;
  }

  setFilter(f: string) {
    this.activeFilter = f;
  }

  toggleDetail(index: number) {
    this.openRowIndex = this.openRowIndex === index ? -1 : index;
  }
}
