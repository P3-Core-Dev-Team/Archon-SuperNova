import os
import re

app_ts = 'src/app/app.component.ts'
with open(app_ts, 'r') as f:
    at = f.read()

# Fix fetchData method
at = re.sub(
    r'  fetchData\(\) \{[\s\S]*?\}\n',
    '  fetchData() {\n    if (document.getElementById("p-admin-users")?.classList.contains("on")) { this.fetchUsers(); this.fetchGroups(); }\n    else if (document.getElementById("p-admin-groups")?.classList.contains("on")) this.fetchGroups();\n  }\n',
    at
)

# Fix go method
at = re.sub(
    r'    // Trigger specific APIs based on screen selection[\s\S]*?if \(id === \'dashboard\'\) \{[\s\S]*?    \} else if \(id === \'ds-list\'\) \{[\s\S]*?    \} else if \(id === \'jobs-all\'\) \{[\s\S]*?    \} else if \(id === \'settings-tpl\'\) \{[\s\S]*?    \} else if \(id === \'settings-system\'\) \{',
    '    if (id === \'settings-system\') {',
    at
)

# Append closing brace
at += '}\n'

with open(app_ts, 'w') as f:
    f.write(at)

