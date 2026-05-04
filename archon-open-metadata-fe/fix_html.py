import os

html_path = 'src/app/features/job-template-manager/job-template-manager.component.html'
with open(html_path, 'r') as f:
    c = f.read()

c = c.replace('editId', 'currentTplId')
c = c.replace('templateName', 'newTpl.name')

with open(html_path, 'w') as f:
    f.write(c)
