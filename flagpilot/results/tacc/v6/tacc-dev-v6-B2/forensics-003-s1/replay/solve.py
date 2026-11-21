with open('challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# The file contains two types of whitespace:
# - Regular space (U+0020) = 1
# - EM SPACE (U+2003) = 0
# This is a binary encoding where the two different space characters represent bits.

bits = []
for c in text:
    if c == ' ':
        bits.append(1)
    elif c == '\u2003':
        bits.append(0)

# Convert bits to bytes
result = []
for i in range(0, len(bits) - 7, 8):
    byte = 0
    for j in range(8):
        byte = (byte << 1) | bits[i+j]
    result.append(byte)

full_text = bytes(result).decode('utf-8', errors='replace')

# Extract the flag
import re
match = re.search(r'picoCTF\{[^}]+\}', full_text)
if match:
    print(match.group(0))
else:
    print(full_text)