import re

file_path = 'src/app/app.component.html'
with open(file_path, 'r') as f:
    content = f.read()

# Replace (click)="go(...)" with routerLink="..."
content = content.replace('(click)="go(\'dashboard\', $event.currentTarget, \'Overview\', \'Dashboard\')"', 'routerLink="/dashboard" routerLinkActive="active"')
content = content.replace('(click)="go(\'ds-list\', $event.currentTarget, \'Data\', \'Datasources\')"', 'routerLink="/datasource" routerLinkActive="active"')
content = content.replace('(click)="go(\'jobs-all\', $event.currentTarget, \'Job profiles\', \'All jobs\')"', 'routerLink="/job-profile" routerLinkActive="active"')

# Replace audit and other links
content = content.replace('(click)="go(\'audit-log\', $event.currentTarget, \'Governance\', \'Audit log\')"', 'routerLink="/audit" routerLinkActive="active"')
content = content.replace('(click)="go(\'settings-tpl\', $event.currentTarget, \'Configuration\', \'Job templates\')"', 'routerLink="/settings/job-templates" routerLinkActive="active"')
content = content.replace('(click)="go(\'admin-users\', $event.currentTarget, \'Administration\', \'Users\')"', 'routerLink="/admin" routerLinkActive="active"')

# For other empty dummy pages, map to dashboard for now or keep them as is
# Actually, the user just wants the basic routing.

# Replace the manual components with router-outlet
components_str = """      <app-dashboard (viewJobEvt)="go('jobs-all', null, 'Jobs', 'Job profiles')"></app-dashboard>
      <app-datasource-manager></app-datasource-manager>
      <app-job-profile></app-job-profile>
      <app-job-template-manager></app-job-template-manager>"""

content = content.replace(components_str, "      <router-outlet></router-outlet>")

with open(file_path, 'w') as f:
    f.write(content)

# Update styles.css
styles_path = 'src/styles.css'
with open(styles_path, 'r') as f:
    styles = f.read()

styles = styles.replace('.page{display:none}', '.page{display:block; animation: fadeIn 0.2s ease-out}')
styles += "\n@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }\n"

with open(styles_path, 'w') as f:
    f.write(styles)

