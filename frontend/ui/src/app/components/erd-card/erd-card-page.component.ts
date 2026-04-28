// Tiny route wrapper -- pulls jobId from the URL and feeds it into the
// ErdCardComponent (which expects a signal input). Keeping this separate
// from ErdCardComponent lets the ERD component be embedded in other
// places (a tab, a side panel, etc.) without forcing them to deal with
// route parsing.
import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ErdCardComponent } from './erd-card.component';

@Component({
  selector: 'app-erd-card-page',
  standalone: true,
  imports: [CommonModule, RouterLink, ErdCardComponent],
  template: `
    <a [routerLink]="['/jobs', jobId()]" class="back">&larr; Back to job detail</a>
    <h2 class="title">ERD card view</h2>
    @if (jobId()) {
      <app-erd-card [jobId]="jobId()!" />
    } @else {
      <p class="muted">Missing job id in URL.</p>
    }
  `,
  styles: [`
    .back { color: #656d76; font-size: 13px; text-decoration: none; }
    .back:hover { color: #0969da; text-decoration: underline; }
    .title { margin: 12px 0 18px; }
    .muted { color: #656d76; }
  `],
})
export class ErdCardPageComponent {
  private route = inject(ActivatedRoute);
  jobId = signal<string | null>(this.route.snapshot.paramMap.get('id'));
}
