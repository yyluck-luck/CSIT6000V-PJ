s = open('./challenge/encrypted.txt').read().strip()
for shift in range(1,26):
    out = ""
    for c in s:
        if c.isalpha():
            base = ord('A') if c.isupper() else ord('a')
            out += chr((ord(c)-base+shift)%26+base)
        else:
            out += c
    if out.startswith("picoCTF"):
        print(out)
        break