import re

with open('challenge/whitepages_task_3.txt', 'rb') as f:
    data = f.read()

text = data.decode('utf-8')
# EM SPACE (U+2003) = 0, regular space = 1
pattern = ''.join(['0' if c == '\u2003' else '1' for c in text])

result = []
for i in range(0, len(pattern) - 7, 8):
    byte_str = pattern[i:i+8]
    val = int(byte_str, 2)
    if 32 <= val <= 126:
        result.append(chr(val))
    else:
        result.append(f'[{val}]')

full_text = ''.join(result)
match = re.search(r'picoCTF\{[^}]+\}', full_text)
if match:
    print(match.group())
else:
    print(full_text)