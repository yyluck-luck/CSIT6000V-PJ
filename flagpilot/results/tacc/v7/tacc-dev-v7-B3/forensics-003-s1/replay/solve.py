import re

with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# Regular space (U+0020) = 1, EM space (U+2003) = 0
bits = ''.join('1' if c == ' ' else '0' for c in text)

# Convert bits to bytes
result = []
for i in range(0, len(bits) - 7, 8):
    byte = int(bits[i:i+8], 2)
    result.append(byte)

output = bytes(result).decode('ascii', errors='replace')

# Extract flag
match = re.search(r'picoCTF\{[^}]+\}', output)
if match:
    print(match.group())
