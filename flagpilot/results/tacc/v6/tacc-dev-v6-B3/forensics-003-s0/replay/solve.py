import re

with open('challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# Convert to bits: EM SPACE (U+2003) = 0, regular space = 1 (inverted)
bits = []
for c in text:
    if c == '\u2003':
        bits.append(0)
    else:
        bits.append(1)

# Convert bits to bytes
bytes_data = bytearray()
for i in range(0, len(bits) - 7, 8):
    byte = 0
    for j in range(8):
        byte = (byte << 1) | bits[i+j]
    bytes_data.append(byte)

# Extract the flag
result = bytes_data.decode('ascii', errors='replace')
match = re.search(r'picoCTF\{[^}]+\}', result)
if match:
    print(match.group(0))
else:
    print(result)