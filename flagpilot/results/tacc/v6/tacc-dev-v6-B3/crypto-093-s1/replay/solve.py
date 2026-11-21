import re

with open('./challenge/message.txt', 'r') as f:
    s = f.read().strip()

result = ""
for i in range(0, len(s), 3):
    block = s[i:i+3]
    rotated = block[-1] + block[:-1]
    result += rotated

match = re.search(r'picoCTF\{[^}]+\}', result)
print(match.group())