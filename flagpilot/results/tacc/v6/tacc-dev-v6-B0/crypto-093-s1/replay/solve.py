import re

with open('./challenge/message.txt', 'r') as f:
    s = f.read().strip()

n = len(s)

# Try all grid dimensions and row shifts to find the one that produces picoCTF
found = None
for rows in range(1, n + 1):
    cols = n // rows
    if rows * cols != n:
        continue
    for shift in range(cols):
        grid = [s[i * cols:(i + 1) * cols] for i in range(rows)]
        result = ''
        for r in range(rows):
            row = grid[r]
            shifted = row[shift:] + row[:shift]
            result += shifted
        if 'picoCTF{' in result:
            found = result
            break
    if found:
        break

if found:
    m = re.search(r'picoCTF\{[^}]*\}', found)
    if m:
        print(m.group(0))
    else:
        print(found)
else:
    print("Flag not found")
