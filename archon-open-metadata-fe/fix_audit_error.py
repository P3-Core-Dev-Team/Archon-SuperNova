import os
import glob

for f_name in glob.glob('../archon-open-metadata_be/src/main/java/com/archon/openmetadata/job/controllers/*.java'):
    with open(f_name, 'r') as f:
        content = f.read()
    if 'setEventType' in content:
        content = content.replace('audit.setEventType(', 'audit.setAction(')
        with open(f_name, 'w') as f:
            f.write(content)
