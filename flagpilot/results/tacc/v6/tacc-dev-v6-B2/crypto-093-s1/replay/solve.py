import re

with open('challenge/message.txt', 'r') as f:
    s = f.read().strip()

# Each block of 3 characters was rotated left by 2; reverse by rotating right by 2 (or left by 1)
blocks = [s[i:i+3] for i in range(0, len(s), 3)]
result = []
for b in blocks:
    # rotate right by 2 (equivalent to left by len-2)
    r = 2
    rotated = b[-r:] + b[:-r]
    result.append(rotated)
msg = ''.join(result)
print(msg)

flag = re.search(r'picoCTF\{.*\}', msg).group()
print(flag)