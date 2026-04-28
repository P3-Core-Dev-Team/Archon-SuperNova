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
      background: #ffffff;
      border-bottom: 1px solid #d0d7de;
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
      color: #656d76;
      padding: 4px 8px;
      border-radius: 4px;
      transition: color 0.1s, background 0.1s;
    }
    nav a:hover { color: #1f2328; text-decoration: none; }
    nav a.active { color: #1f2328; background: #f6f8fa; }
    main {
      padding: 24px 28px 60px;
      max-width: 1400px;
      margin: 0 auto;
    }
  `],
})
export class AppComponent {}
