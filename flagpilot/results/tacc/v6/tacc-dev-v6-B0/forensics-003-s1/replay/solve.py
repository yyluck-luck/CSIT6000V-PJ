with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# space=1, em_space=0
binary = ''.join('1' if c == ' ' else '0' for c in text)

# Pad to multiple of 8
padded = binary.ljust((len(binary) + 7) // 8 * 8, '0')
byte_val = int(padded, 2)
byte_str = byte_val.to_bytes(len(padded)//8, 'big')

result = byte_str.decode('ascii')
print(result)

# Extract flag
import re
match = re.search(r'picoCTF\{[^}]+\}', result)
if match:
    print(match.group(0))