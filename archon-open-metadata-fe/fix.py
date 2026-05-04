import os

app_ts = 'src/app/app.component.ts'
with open(app_ts, 'r') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    if 'toggleTheme() {' in line:
        pass
    if line.strip() == '}' and i == 181: # line 182 is index 181
        new_lines.append('}\n')
        break

with open(app_ts, 'w') as f:
    f.writelines(new_lines)
