import re

file_path = 'src/app/app.component.ts'
with open(file_path, 'r') as f:
    content = f.read()

# We can just remove fetchData() entirely, and remove the ViewChild imports/declarations
content = re.sub(r"  @ViewChild\(.*?\).*?\n", "", content)
content = re.sub(r"  fetchData\(\) \{[\s\S]*?  \}", "", content)

# Remove the unused imports
content = content.replace("import { DashboardComponent } from './features/dashboard/dashboard.component';", "")
content = content.replace("import { DatasourceManagerComponent } from './features/datasource-manager/datasource-manager.component';", "")
content = content.replace("import { JobProfileComponent } from './features/job-profile/job-profile.component';", "")
content = content.replace("import { JobTemplateManagerComponent } from './features/job-template-manager/job-template-manager.component';", "")

with open(file_path, 'w') as f:
    f.write(content)

