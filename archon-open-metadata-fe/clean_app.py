import os
import re

app_ts = 'src/app/app.component.ts'
with open(app_ts, 'r') as f:
    at = f.read()

# Remove the inner method calls that will fail compilation
at = re.sub(r'this\.fetchDatasources\(\);', '', at)
at = re.sub(r'this\.fetchJobs\(\);', '', at)
at = re.sub(r'this\.fetchJobTemplates\(\);', '', at)

with open(app_ts, 'w') as f:
    f.write(at)
