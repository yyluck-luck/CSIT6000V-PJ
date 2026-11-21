with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

binary = ''
i = 0
while i < len(data):
    if data[i] == 0x20:
        binary += '1'
        i += 1
    elif data[i] == 0xe2 and i+2 < len(data) and data[i+1] == 0x80 and data[i+2] == 0x83:
        binary += '0'
        i += 3
    else:
        i += 1

result = ''
for i in range(0, len(binary), 8):
    byte = int(binary[i:i+8], 2)
    result += chr(byte)

import re
match = re.search(r'picoCTF\{[^}]+\}', result)
if match:
    print(match.group(0))