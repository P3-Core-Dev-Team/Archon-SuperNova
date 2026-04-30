import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="layout">
      <header class="topbar">
        <h1 class="brand">Archon-SuperNova</h1>
        <nav>
          <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{exact: true}">Dashboard</a>
          <a routerLink="/submit" routerLinkActive="active">Submit</a>
          <a routerLink="/jobs" routerLinkActive="active">Jobs</a>
        </nav>
      </header>
      <main>
        <router-outlet />
      </main>
    </div>
  `,
  styles: [`
    .layout { min-height: 100vh; }
    .topbar {
      display: flex;
      align-items: center;
      gap: 32px;
      padding: 14px 28px;
      background: #161b22;
      border-bottom: 1px solid #30363d;
    }
    .brand {
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      letter-spacing: 0.3px;
    }
    nav {
      display: flex;
      gap: 16px;
    }
    nav a {
      color: #8b949e;
      padding: 4px 8px;
      border-radius: 4px;
      transition: color 0.1s, background 0.1s;
    }
    nav a:hover { color: #e6edf3; text-decoration: none; }
    nav a.active { color: #e6edf3; background: #21262d; }
    /* Page content fills the viewport.  Per-route components (Dashboard,
       Submit, Jobs list) carry their own max-width via a host-level
       class so wide screens don't stretch their text columns; the job
       detail route deliberately stays unconstrained so graphs, ERDs,
       and cluster cards can use the full width. */
    main {
      padding: 24px 24px 60px;
    }
  `],
})
export class AppComponent {}
