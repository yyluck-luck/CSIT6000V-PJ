data = open('./challenge/whitepages_task_3.txt','rb').read()
bits = []
i = 0
while i < len(data):
    if data[i:i+3] == b'\xe2\x80\x83':
        bits.append('0')
        i += 3
    elif data[i] == 0x20:
        bits.append('1')
        i += 1
    else:
        i += 1

byte_str = ''
for j in range(0, len(bits) - (len(bits) % 8), 8):
    byte_val = int(''.join(bits[j:j+8]), 2)
    byte_str += chr(byte_val)

import re
m = re.search(r'picoCTF\{[^}]+\}', byte_str)
print(m.group(0))