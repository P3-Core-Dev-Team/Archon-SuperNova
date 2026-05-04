import re

ts_path = 'src/app/app.component.ts'
with open(ts_path, 'r') as f:
    ts_content = f.read()

if 'sidebarCollapsed' not in ts_content:
    ts_content = ts_content.replace('isDarkTheme: boolean = false;', 'isDarkTheme: boolean = false;\n  sidebarCollapsed: boolean = false;')
    with open(ts_path, 'w') as f:
        f.write(ts_content)

html_path = 'src/app/app.component.html'
with open(html_path, 'r') as f:
    html = f.read()

if '[class.collapsed]="sidebarCollapsed"' not in html:
    html = html.replace('<div class="sidebar">', '<div class="sidebar" [class.collapsed]="sidebarCollapsed">')

    # Add the collapse button at the bottom of the sidebar
    collapse_btn = """
    <div style="margin-top: auto; padding: 12px; border-top: 1px solid var(--color-border-tertiary);">
      <div class="nav-item" (click)="sidebarCollapsed = !sidebarCollapsed" style="display: flex; gap: 10px; align-items: center; color: var(--color-text-secondary); cursor: pointer; padding: 8px;">
        <svg style="width: 16px; height: 16px; stroke: currentColor; fill: none; stroke-width: 1.5;" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>
        <span class="nav-label" style="font-size: 13px; font-weight: 500;">Menu options</span>
      </div>
    </div>
"""
    # Insert right before the end of the sidebar
    # The sidebar ends before `<div class="content">`
    html = html.replace('    </div>\n\n    <div class="content">', collapse_btn + '    </div>\n\n    <div class="content">')
    with open(html_path, 'w') as f:
        f.write(html)

css_path = 'src/styles.css'
with open(css_path, 'r') as f:
    css = f.read()

if '.sidebar.collapsed' not in css:
    collapse_css = """
/* Sidebar Collapsed State */
.sidebar { transition: width 0.2s ease; }
.sidebar.collapsed { width: 64px; }
.sidebar.collapsed .brand { padding: 0; justify-content: center; }
.sidebar.collapsed .brand-text,
.sidebar.collapsed .nav-label,
.sidebar.collapsed .nav-group-label,
.sidebar.collapsed .nav-badge,
.sidebar.collapsed .nav-expand,
.sidebar.collapsed .nav-child { display: none !important; }
.sidebar.collapsed .nav-item { justify-content: center; padding: 10px 0; }
.sidebar.collapsed .nav-icon { margin: 0; }
"""
    css += collapse_css
    with open(css_path, 'w') as f:
        f.write(css)

