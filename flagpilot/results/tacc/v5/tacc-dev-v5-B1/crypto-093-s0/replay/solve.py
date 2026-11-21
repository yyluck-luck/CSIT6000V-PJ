import re

with open('./challenge/message.txt', 'r') as f:
    msg = f.read().strip()

bs = 3
rot = 2
blocks = [msg[i:i+bs] for i in range(0, len(msg), bs)]
result = ''.join(b[rot:] + b[:rot] for b in blocks)

flag = re.search(r'picoCTF\{[^}]+\}', result).group()
print(flag)