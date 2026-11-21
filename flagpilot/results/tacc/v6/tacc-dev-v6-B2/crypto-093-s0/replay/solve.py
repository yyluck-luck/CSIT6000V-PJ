with open('challenge/message.txt', 'r') as f:
    text = f.read().strip()

block_size = 3
shift = 2
result = ''
for i in range(0, len(text), block_size):
    block = text[i:i+block_size]
    rotated = block[shift:] + block[:shift]
    result += rotated

# Extract the flag
import re
match = re.search(r'picoCTF\{[^}]+\}', result)
if match:
    print(match.group(0))
else:
    print(result)