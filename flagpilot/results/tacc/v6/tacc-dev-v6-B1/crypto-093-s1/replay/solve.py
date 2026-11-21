import re

with open('./challenge/message.txt', 'r') as f:
    s = f.read().strip()

block_size = 3
rot = 1
result = ''
for i in range(0, len(s), block_size):
    block = s[i:i+block_size]
    rotated = block[-rot:] + block[:-rot]
    result += rotated

flag = re.search(r'picoCTF\{[^}]+\}', result).group()
print(flag)