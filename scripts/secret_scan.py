import os, re, sys
patterns = [
    r'API_KEY\s*=\s*.{1,50}',
    r'API_SECRET\s*=\s*.{1,50}',
    r'SECRET_KEY\s*=\s*.{1,50}',
    r'PASSWORD\s*=\s*.{1,50}',
    r'TOKEN\s*=\s*.{1,50}',
]
matches = []
for root, dirs, files in os.walk('src'):
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            with open(path) as fh:
                for i, line in enumerate(fh, 1):
                    for pat in patterns:
                        if re.search(pat, line):
                            matches.append((path, i, line.strip()))
if matches:
    for path, line_num, line in matches:
        print(f'Potential secret in {path}:{line_num}: {line}')
    sys.exit(1)
else:
    print('No potential secrets detected by patterns')
    sys.exit(0)
