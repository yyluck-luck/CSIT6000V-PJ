import re
with open('challenge/flag') as f:
    content = f.read()
flag = re.search(r'picoCTF\{[^}]+\}', content).group(0)
print(flag)