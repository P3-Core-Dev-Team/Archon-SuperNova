import os
import re

app_ts = 'src/app/app.component.ts'
with open(app_ts, 'r') as f:
    content = f.read()

# Fix fetchData
content = re.sub(
    r'  fetchData\(\) \{[\s\S]*?  \}',
    '  fetchData() {\n    if (document.getElementById("p-admin-users")?.classList.contains("on")) { this.fetchUsers(); this.fetchGroups(); }\n    else if (document.getElementById("p-admin-groups")?.classList.contains("on")) this.fetchGroups();\n  }',
    content
)

# Strip out trailing methods from the old layout
if 'createJob(payload: any) {' in content:
    idx = content.find('createJob(payload: any) {')
    # find toggleTheme to see if it's there
    idx_toggle = content.find('toggleTheme() {')
    if idx_toggle != -1 and idx_toggle < idx:
        # we can chop after toggleTheme
        pass

# I'll just write it back
with open(app_ts, 'w') as f:
    f.write(content)
