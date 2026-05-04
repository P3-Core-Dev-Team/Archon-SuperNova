import os

html_path = 'src/app/features/job-template-manager/job-template-manager.component.html'
with open(html_path, 'r') as f:
    c = f.read()

# Replace resetForm() with setting isEditing=false and currentTplId=null, and newTpl cleared
c = c.replace('resetForm(); showForm = true', "isEditing=false; currentTplId=null; newTpl={name:'', options:[]}; showForm=true")
c = c.replace('let stage of stages', 'let stage of newTpl.options')

with open(html_path, 'w') as f:
    f.write(c)

