import base64
# The challenge provides the base64-encoded string as the puzzle input
challenge_input = 'bDNhcm5fdGgzX3IwcDM1'
decoded = base64.b64decode(challenge_input).decode()
print(f'picoCTF{{{decoded}}}')