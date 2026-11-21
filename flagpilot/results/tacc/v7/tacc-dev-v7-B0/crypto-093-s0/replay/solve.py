with open('./challenge/message.txt') as f:
    s = f.read().strip()
blocks = [s[i:i+3] for i in range(0, len(s), 3)]
perm = (2, 0, 1)
result = ''
for b in blocks:
    result += b[perm[0]] + b[perm[1]] + b[perm[2]]
# Extract the flag
import re
m = re.search(r'picoCTF\{[^}]+\}', result)
print(m.group(0))