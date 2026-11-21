import subprocess
result = subprocess.run(['zcat', './challenge/dds1-alpine.flag.img.gz'], capture_output=True)
output = result.stdout.decode('latin-1')
import re
match = re.search(r'picoCTF\{[^}]+\}', output)
print(match.group(0))