import base64, os, glob

# Read all files in challenge directory
found = None
for f in glob.glob('./challenge/*'):
    try:
        with open(f, 'r') as fh:
            content = fh.read().strip()
            if content:
                found = content
                break
    except:
        pass

# The base64 string from the challenge description
b64_str = 'bDNhcm5fdGgzX3IwcDM1'
if found:
    b64_str = found

decoded = base64.b64decode(b64_str).decode()
print(f'picoCTF{{{decoded}}}')