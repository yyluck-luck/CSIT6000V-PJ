s = open('challenge/message.txt').read().rstrip('\n')
result = []
for i in range(0, len(s), 3):
    block = s[i:i+3]
    recovered = block[2] + block[0] + block[1]
    result.append(recovered)
msg = ''.join(result)
print(msg)
# Extract flag
import re
flag = re.search(r'picoCTF\{[^}]+\}', msg).group()
print(flag)