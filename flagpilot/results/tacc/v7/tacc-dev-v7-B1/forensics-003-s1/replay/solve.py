with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

# em space (U+2003, e2 80 83) = 0, regular space (0x20) = 1
bits = []
i = 0
while i < len(data):
    if data[i:i+3] == b'\xe2\x80\x83':
        bits.append(0)
        i += 3
    elif data[i] == 0x20:
        bits.append(1)
        i += 1
    else:
        i += 1

result = []
for i in range(0, len(bits) - 7, 8):
    byte = 0
    for j in range(8):
        byte = (byte << 1) | bits[i+j]
    result.append(byte)

printable = bytes(result).decode('latin-1')
import re
m = re.search(r'picoCTF\{[^}]+\}', printable)
print(m.group(0))