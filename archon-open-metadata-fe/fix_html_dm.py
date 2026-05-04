import os

html_path = 'src/app/features/datasource-manager/datasource-manager.component.html'
with open(html_path, 'r') as f:
    c = f.read()

c = c.replace('saveDatasource.emit()', 'saveDatasource()')

with open(html_path, 'w') as f:
    f.write(c)

