s = open('challenge/message.txt').read().rstrip('\n')
result = []
for i in range(0, len(s), 3):
    block = s[i:i+3]
    if len(block) == 3:
        result.append(block[2] + block[0] + block[1])
    else:
        result.append(block)
msg = ''.join(result)
import re
m = re.search(r'picoCTF\{[^}]+\}', msg)
print(m.group(0))