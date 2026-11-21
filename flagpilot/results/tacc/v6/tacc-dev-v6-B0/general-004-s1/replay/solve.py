import re
with open('./challenge/flag') as f:
    content = f.read()
match = re.search(r'picoCTF\{[^}]+\}', content)
print(match.group(0))