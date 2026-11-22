import subprocess
result = subprocess.run(['zcat', './challenge/dds1-alpine.flag.img.gz'], capture_output=True)
result2 = subprocess.run(['strings'], input=result.stdout, capture_output=True)
for line in result2.stdout.decode().splitlines():
    if 'picoCTF' in line:
        print(line.strip())
        break