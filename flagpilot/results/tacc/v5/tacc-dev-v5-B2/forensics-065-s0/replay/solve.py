import subprocess
result = subprocess.run(['zcat', './challenge/dds1-alpine.flag.img.gz'], stdout=subprocess.PIPE)
strings = result.stdout.decode('latin-1')
import re
match = re.search(r'picoCTF\{[^}]+\}', strings)
print(match.group(0))