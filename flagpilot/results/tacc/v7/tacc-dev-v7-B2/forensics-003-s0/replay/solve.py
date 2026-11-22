with open('challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# Regular space = 1, EM space (U+2003) = 0
bits = ''
for ch in text:
    if ch == '\u2003':  # EM SPACE
        bits += '0'
    elif ch == ' ':  # regular space
        bits += '1'

# Convert 8-bit groups to bytes
byte_str = ''.join(format(int(bits[i:i+8], 2), '02x') for i in range(0, len(bits), 8))
decoded = bytes.fromhex(byte_str)

# Extract the flag
import re
match = re.search(r'picoCTF\{[^}]+\}', decoded.decode('utf-8', errors='replace'))
print(match.group(0))
