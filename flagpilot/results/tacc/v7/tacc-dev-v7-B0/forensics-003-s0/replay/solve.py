with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

binary_str = ''
for c in text:
    if c == ' ':
        binary_str += '1'
    elif c == '\u2003':
        binary_str += '0'

while len(binary_str) % 8 != 0:
    binary_str += '0'

result = bytearray()
for i in range(0, len(binary_str), 8):
    byte_val = int(binary_str[i:i+8], 2)
    result.append(byte_val)

print(result.decode('utf-8', errors='replace'))
