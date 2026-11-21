with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# Em space (U+2003) = 0, regular space = 1
bit_str = ''
for ch in text:
    if ch == '\u2003':
        bit_str += '0'
    elif ch == ' ':
        bit_str += '1'

bytes_data = bytearray()
for i in range(0, len(bit_str), 8):
    bytes_data.append(int(bit_str[i:i+8], 2))

result = bytes_data.decode('utf-8', errors='replace')
print(result)
# Extract flag
import re
match = re.search(r'picoCTF\{[^}]+\}', result)
if match:
    print(match.group())