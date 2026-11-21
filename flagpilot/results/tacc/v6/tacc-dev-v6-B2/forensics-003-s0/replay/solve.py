with open('challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()
text = data.decode('utf-8')
bits = ''.join('0' if ch == '\u2003' else '1' for ch in text)
if len(bits) % 8: bits = bits.ljust((len(bits)//8+1)*8, '0')
result = bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
print(result.decode('utf-8', errors='replace'))