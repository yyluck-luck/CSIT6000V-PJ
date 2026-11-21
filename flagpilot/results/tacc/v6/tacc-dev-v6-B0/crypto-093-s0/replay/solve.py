with open('./challenge/message.txt', 'r') as f:
    s = f.read().strip()

block_size = 3
shift = 2
result = ''
for i in range(0, len(s), block_size):
    block = s[i:i+block_size]
    result += block[shift:] + block[:shift]

# Extract the flag
import re
m = re.search(r'picoCTF\{[^}]+\}', result)
if m:
    print(m.group(0))
else:
    print(result)