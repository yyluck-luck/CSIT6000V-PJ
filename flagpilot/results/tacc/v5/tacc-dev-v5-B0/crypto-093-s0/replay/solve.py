with open('./challenge/message.txt', 'r') as f:
    s = f.read().strip()

block_size = 3
result = []
for i in range(0, len(s), block_size):
    block = s[i:i+block_size]
    if len(block) > 1:
        result.append(block[-1] + block[:-1])
    else:
        result.append(block)
decoded = ''.join(result)
print(decoded)

# Extract the flag
import re
match = re.search(r'picoCTF\{[^}]+\}', decoded)
if match:
    print(match.group(0))