import re

with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# Reversed: em-space (U+2003) = 0, regular space (U+0020) = 1
binary_rev = ''.join('0' if ch == '\u2003' else '1' for ch in text)

# Convert to bytes
byte_array = bytearray()
for i in range(0, len(binary_rev), 8):
    if i + 8 <= len(binary_rev):
        byte_array.append(int(binary_rev[i:i+8], 2))

result = byte_array.decode('utf-8', errors='replace')

# Extract the flag
match = re.search(r'picoCTF\{[^}]+\}', result)
if match:
    print(match.group(0))
else:
    print(result)