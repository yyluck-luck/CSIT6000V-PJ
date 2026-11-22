import subprocess
result = subprocess.run(['zcat', './challenge/dds1-alpine.flag.img.gz'], capture_output=True)
import re
match = re.search(rb'picoCTF\{[^}]+\}', result.stdout)
print(match.group().decode())