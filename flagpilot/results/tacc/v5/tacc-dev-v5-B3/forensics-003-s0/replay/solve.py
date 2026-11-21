
with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# EM SPACE (U+2003) = 0, regular space = 1
bits = ''.join('0' if c == '\u2003' else '1' for c in text)

def bits_to_bytes(bits):
    result = []
    for i in range(0, len(bits) - 7, 8):
        byte = int(bits[i:i+8], 2)
        result.append(byte)
    return bytes(result)

b = bits_to_bytes(bits)
content = b.decode('utf-8', errors='replace')

import re
match = re.search(r'picoCTF\{[^}]+\}', content)
if match:
    print(match.group(0))
else:
    print(content)
