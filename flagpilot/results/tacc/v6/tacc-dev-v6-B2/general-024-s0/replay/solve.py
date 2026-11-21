import os, subprocess
os.chdir('challenge')
out = subprocess.run(['python3', 'code.py'], capture_output=True, text=True)
print(out.stdout.strip())