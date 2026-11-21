import base64
# The challenge provides the string 'bDNhcm5fdGgzX3IwcDM1' in its description
# It says it has something to do with bases, so we decode base64
challenge_string = 'bDNhcm5fdGgzX3IwcDM1'
decoded = base64.b64decode(challenge_string).decode()
print(f'picoCTF{{{decoded}}}')