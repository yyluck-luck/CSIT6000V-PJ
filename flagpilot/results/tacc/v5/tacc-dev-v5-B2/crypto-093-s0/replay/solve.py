import re

with open('challenge/message.txt', 'r') as f:
    s = f.read().strip()

block_size = 3
rot = 2
result = ''
for i in range(0, len(s), block_size):
    block = s[i:i+block_size]
    r = block[block_size-rot:] + block[:block_size-rot]
    result += r

# Extract the flag
match = re.search(r'picoCTF\{[^}]+\}', result)
if match:
    print(match.group(0))