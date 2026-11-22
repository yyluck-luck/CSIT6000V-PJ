with open('./challenge/message.txt', 'r') as f:
    msg = f.read().strip()

blocks = [msg[i:i+3] for i in range(0, len(msg), 3)]
result = ''
for b in blocks:
    result += b[2] + b[0] + b[1]

# Extract the flag from the recovered message
import re
m = re.search(r'picoCTF\{[^}]*\}', result)
if m:
    print(m.group(0))
else:
    print(result)