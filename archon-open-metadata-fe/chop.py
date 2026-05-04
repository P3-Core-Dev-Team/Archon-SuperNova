import os

app_ts = 'src/app/app.component.ts'
with open(app_ts, 'r') as f:
    lines = f.readlines()

end_idx = 0
for i, line in enumerate(lines):
    if line.strip() == 'toggleTheme() {':
        pass
    if line.strip() == '}' and i > 173 and i < 185:
        end_idx = i
        break

with open(app_ts, 'w') as f:
    f.writelines(lines[:end_idx + 1])
    f.write('}\n')

