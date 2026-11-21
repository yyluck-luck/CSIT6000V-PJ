with open('challenge/whitepages_task_3.txt', 'r', encoding='utf-8') as f:
    text = f.read()

binary = ''
for ch in text:
    if ch == ' ':
        binary += '1'
    elif ch == '\u2003':
        binary += '0'

padded = binary.ljust((len(binary)//8 + 1) * 8, '0')
chars = []
for i in range(0, len(padded), 8):
    byte = padded[i:i+8]
    chars.append(chr(int(byte, 2)))
result = ''.join(chars)
import re
m = re.search(r'picoCTF\{[^}]+\}', result)
print(m.group(0))