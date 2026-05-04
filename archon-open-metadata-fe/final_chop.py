import os

app_ts = 'src/app/app.component.ts'
with open(app_ts, 'r') as f:
    content = f.read()

idx = content.find('toggleTheme() {')
if idx != -1:
    # find the matching closing brace for toggleTheme
    brace_count = 0
    in_method = False
    cut_idx = -1
    for i in range(idx, len(content)):
        if content[i] == '{':
            brace_count += 1
            in_method = True
        elif content[i] == '}':
            brace_count -= 1
        
        if in_method and brace_count == 0:
            cut_idx = i
            break
            
    if cut_idx != -1:
        new_content = content[:cut_idx + 1] + '\n}\n'
        with open(app_ts, 'w') as f:
            f.write(new_content)
