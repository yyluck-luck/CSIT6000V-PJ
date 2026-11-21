with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

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

result = []
for i in range(0, len(bits) - 7, 8):
    byte = int(''.join(bits[i:i+8]), 2)
    result.append(byte)

printable = bytes(result).decode('latin-1')
print(printable)