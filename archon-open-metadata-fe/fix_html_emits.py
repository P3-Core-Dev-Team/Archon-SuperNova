import os
import glob

for f_name in glob.glob('src/app/features/**/*.html', recursive=True):
    with open(f_name, 'r') as f:
        content = f.read()
    
    # Datasource manager fixes
    content = content.replace('testConnection.emit()', 'testConnection()')
    content = content.replace('deleteDatasource.emit(ds.id)', 'deleteDatasource(ds.id)')
    content = content.replace('editDs.emit(ds)', 'editDatasource(ds)')
    content = content.replace('resetDsEvt.emit()', 'resetDsForm()')
    content = content.replace('saveDatasourceEvt.emit()', 'saveDatasource()')

    # Job template manager fixes
    content = content.replace('deleteTplEvt.emit(tpl.id)', 'deleteTemplate(tpl.id)')

    # Dashboard fixes
    content = content.replace('viewJobEvt.emit(job.id)', 'viewJob(job.id)')

    # Job profile fixes
    content = content.replace('deleteJobEvt.emit(job.id)', 'deleteJob(job.id)')
    content = content.replace('createJobEvt.emit()', 'createJob()')
    
    with open(f_name, 'w') as f:
        f.write(content)

