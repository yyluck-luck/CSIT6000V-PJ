with open('./challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')

# Em space = 0, regular space = 1
bits = []
for ch in text:
    if ch == '\u2003':  # em space
        bits.append('0')
    elif ch == ' ':  # regular space
        bits.append('1')

bit_string = ''.join(bits)

bytes_data = bytearray()
for i in range(0, len(bit_string), 8):
    byte_val = int(bit_string[i:i+8], 2)
    bytes_data.append(byte_val)

result = bytes_data.decode('utf-8', errors='replace')
print(result)

# Extract flag
import re
match = re.search(r'picoCTF\{[^}]+\}', result)
if match:
    print(f"Flag: {match.group()}")