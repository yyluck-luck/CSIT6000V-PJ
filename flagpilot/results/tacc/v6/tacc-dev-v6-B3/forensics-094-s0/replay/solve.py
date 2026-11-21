import subprocess
result = subprocess.run(['strings', './challenge/garden.jpg'], capture_output=True, text=True)
for line in result.stdout.splitlines():
    if 'picoCTF{' in line:
        # Extract the flag from the line
        start = line.index('picoCTF{')
        end = line.index('}', start) + 1
        print(line[start:end])
        break